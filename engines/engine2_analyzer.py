

import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any

from config import (
    E2_CANDLE_INTERVALS, E2_CANDLE_LIMIT,
    E2_CONSENSUS_THRESHOLD, E2_CONFIRMATION_RULES,
    E5_ATR_PERIOD,
)
from models.signal import RawSignal, AnalyzedSignal
from engines.engine5_indicators import Engine5Indicators
from utils.binance_client import BinanceClient
from utils.logger import get_logger

log = get_logger("Engine2-Analyzer")

# Timeframe weights (higher TF = more weight)
TF_WEIGHTS: Dict[str, float] = {
    "1m" : 0.05,
    "5m" : 0.10,
    "15m": 0.15,
    "1h" : 0.25,
    "4h" : 0.30,
    "1d" : 0.15,
}


class Engine2Analyzer:
    """
    Usage:
        engine2 = Engine2Analyzer(on_signal_callback)
        await engine2.process(raw_signals)
    """

    def __init__(self, on_signal: Callable[[AnalyzedSignal], Any]):
        self.on_signal  = on_signal
        self.binance    = BinanceClient()
        self.indicators = Engine5Indicators()
        self._cache: Dict[str, Dict] = {}  # symbol → {tf → df}

    # ══════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ══════════════════════════════════════════════════════════════

    async def process(self, raw_signals: List[RawSignal]) -> None:
        """Process a batch of raw signals from Engine 1."""
        if not raw_signals:
            return

        # Deduplicate by (symbol, direction) — keep highest confidence
        deduplicated = self._deduplicate(raw_signals)
        log.info("[E2] Processing %d unique signals (from %d raw)",
                 len(deduplicated), len(raw_signals))

        # Filter out UNKNOWN / NEUTRAL low-confidence
        filtered = [
            s for s in deduplicated
            if s.symbol != "UNKNOWN" and s.direction in ("LONG", "SHORT")
        ]
        log.info("[E2] %d signals after symbol/direction filter", len(filtered))

        # Analyze each in parallel (cap concurrency to 5)
        sem = asyncio.Semaphore(5)
        tasks = [self._analyze_one(sig, sem) for sig in filtered]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        emitted = 0
        for res in results:
            if isinstance(res, Exception):
                log.error("[E2] Analysis error: %s", res)
                continue
            if res is None:
                continue
            emitted += 1
            log.info("[E2] ✅ Signal approved  %s %s  conf=%.2f",
                     res.symbol, res.direction, res.overall_confidence)
            await self._emit(res)

        log.info("[E2] Emitted %d confirmed signals to Engine 3", emitted)

    # ══════════════════════════════════════════════════════════════
    # CORE ANALYSIS
    # ══════════════════════════════════════════════════════════════

    async def _analyze_one(self, raw: RawSignal,
                            sem: asyncio.Semaphore) -> Optional[AnalyzedSignal]:
        async with sem:
            try:
                return await self._do_analyze(raw)
            except Exception as e:
                log.error("[E2] _analyze_one %s: %s", raw.symbol, e, exc_info=True)
                return None

    async def _do_analyze(self, raw: RawSignal) -> Optional[AnalyzedSignal]:
        symbol = raw.symbol

        # ── 1. Fetch OHLCV for each timeframe ─────────────────────
        tf_data = await self._fetch_all_timeframes(symbol)
        if not tf_data:
            log.warning("[E2] No OHLCV data for %s — skipping", symbol)
            return None

        # ── 2. Run Engine 5 on each timeframe ─────────────────────
        tf_scores: Dict[str, Dict] = {}
        for tf, df in tf_data.items():
            try:
                tf_scores[tf] = self.indicators.analyze(df, raw.direction)
            except Exception as e:
                log.warning("[E2] Indicator error %s %s: %s", symbol, tf, e)

        if not tf_scores:
            return None

        # ── 3. Weighted multi-timeframe consensus ──────────────────
        weighted_consensus = 0.0
        tf_agreement       = 0
        tf_total           = 0
        indicator_snapshot: Dict[str, Any] = {}

        for tf, score_dict in tf_scores.items():
            w   = TF_WEIGHTS.get(tf, 0.1)
            con = score_dict.get("consensus_score", 0)
            weighted_consensus += con * w
            tf_total           += 1
            # count TFs that agree with the signal direction
            if raw.direction == "LONG"  and con > 0.1:  tf_agreement += 1
            if raw.direction == "SHORT" and con < -0.1: tf_agreement += 1
            indicator_snapshot[tf] = {
                k: v for k, v in score_dict.items()
                if k in ("rsi","macd","adx","atr","consensus_score",
                         "market_structure","ema9","ema21","supertrend_dir")
            }

        timeframe_agreement = tf_agreement / tf_total if tf_total else 0

        # ── 4. Current price + ATR from 1h ─────────────────────────
        try:
            current_price = await self.binance.get_price(symbol)
        except Exception:
            current_price = raw.entry_price or 0.0
        if current_price == 0:
            return None

        # Use 1h ATR for SL/TP calculation
        atr_1h = tf_scores.get("1h", {}).get("atr") or \
                 tf_scores.get("4h", {}).get("atr") or \
                 tf_scores.get("15m", {}).get("atr") or \
                 current_price * 0.01

        # ── 5. Compute SL / TP if not provided ────────────────────
        entry_price = raw.entry_price if raw.entry_price else current_price
        if entry_price == 0:
            entry_price = current_price

        sl, tps = self._compute_sl_tp(
            direction   = raw.direction,
            entry_price = entry_price,
            atr         = atr_1h,
            raw_sl      = raw.stop_loss,
            raw_tps     = raw.take_profits,
        )

        # ── 6. Volume confirmation ─────────────────────────────────
        volume_confirmed = self._check_volume(tf_scores)

        # ── 7. Trend alignment ─────────────────────────────────────
        trend_aligned = self._check_trend(tf_scores, raw.direction)

        # ── 8. Overall confidence ──────────────────────────────────
        # combine: signal confidence + TF agreement + indicator consensus
        # + volume + trend
        direction_sign = 1 if raw.direction == "LONG" else -1
        indicator_score = abs(weighted_consensus) * (
            1 if weighted_consensus * direction_sign > 0 else -1
        )
        indicator_score = max(0.0, min(1.0, (indicator_score + 1) / 2))

        overall = (
            raw.confidence        * 0.20 +
            timeframe_agreement   * 0.30 +
            indicator_score       * 0.30 +
            (0.10 if volume_confirmed else 0.0) +
            (0.10 if trend_aligned    else 0.0)
        )
        overall = round(min(1.0, max(0.0, overall)), 4)

        # ── 9. Apply consensus threshold ──────────────────────────
        if E2_CONFIRMATION_RULES and overall < E2_CONSENSUS_THRESHOLD:
            log.info("[E2] Signal %s %s rejected — conf=%.3f < threshold=%.2f",
                     symbol, raw.direction, overall, E2_CONSENSUS_THRESHOLD)
            return None

        # ── 10. Market context ─────────────────────────────────────
        market_context = await self._build_market_context(symbol)

        return AnalyzedSignal(
            raw_signal          = raw,
            symbol              = symbol,
            direction           = raw.direction,
            entry_price         = round(entry_price, 8),
            stop_loss           = round(sl, 8),
            take_profits        = [round(t, 8) for t in tps],
            timeframe_agreement = round(timeframe_agreement, 4),
            indicator_score     = round(indicator_score, 4),
            volume_confirmed    = volume_confirmed,
            trend_aligned       = trend_aligned,
            overall_confidence  = overall,
            market_context      = market_context,
            indicator_snapshot  = indicator_snapshot,
            created_at          = datetime.utcnow(),
        )

    # ══════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════

    async def _fetch_all_timeframes(self, symbol: str) -> Dict:
        tf_data = {}
        tasks = {
            tf: self.binance.get_klines(symbol, tf, E2_CANDLE_LIMIT)
            for tf in E2_CANDLE_INTERVALS
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for tf, res in zip(tasks.keys(), results):
            if isinstance(res, Exception):
                log.warning("[E2] Klines error %s %s: %s", symbol, tf, res)
            elif res is not None and len(res) > 30:
                tf_data[tf] = res
        return tf_data

    @staticmethod
    def _compute_sl_tp(direction: str, entry_price: float, atr: float,
                       raw_sl: Optional[float],
                       raw_tps: List[float]) -> tuple:
        from config import E3_ATR_MULTIPLIER_SL, E3_ATR_MULTIPLIER_TP
        if direction == "LONG":
            sl  = raw_sl if raw_sl and raw_sl < entry_price \
                  else entry_price - E3_ATR_MULTIPLIER_SL * atr
            tps = raw_tps if raw_tps else [
                entry_price + E3_ATR_MULTIPLIER_TP * atr,
                entry_price + E3_ATR_MULTIPLIER_TP * 1.5 * atr,
                entry_price + E3_ATR_MULTIPLIER_TP * 2.0 * atr,
            ]
        else:
            sl  = raw_sl if raw_sl and raw_sl > entry_price \
                  else entry_price + E3_ATR_MULTIPLIER_SL * atr
            tps = raw_tps if raw_tps else [
                entry_price - E3_ATR_MULTIPLIER_TP * atr,
                entry_price - E3_ATR_MULTIPLIER_TP * 1.5 * atr,
                entry_price - E3_ATR_MULTIPLIER_TP * 2.0 * atr,
            ]
        return sl, tps

    @staticmethod
    def _check_volume(tf_scores: Dict) -> bool:
        """Volume confirmed if OBV trend and CMF agree on at least 2 TFs."""
        count = 0
        for tf in ["1h", "4h", "15m"]:
            s = tf_scores.get(tf, {})
            if s.get("obv_trend", 0) == 1 and s.get("cmf", 0) > 0:
                count += 1
            elif s.get("obv_trend", 0) == -1 and s.get("cmf", 0) < 0:
                count += 1
        return count >= 2

    @staticmethod
    def _check_trend(tf_scores: Dict, direction: str) -> bool:
        """Trend aligned if 1h and 4h market_structure matches direction."""
        aligned = 0
        for tf in ["1h", "4h", "1d"]:
            struct = tf_scores.get(tf, {}).get("market_structure", "")
            if direction == "LONG"  and struct == "BULLISH": aligned += 1
            if direction == "SHORT" and struct == "BEARISH": aligned += 1
        return aligned >= 2

    async def _build_market_context(self, symbol: str) -> Dict:
        ctx = {}
        try:
            ctx["funding_rate"]   = await self.binance.get_funding_rate(symbol)
            ctx["open_interest"]  = await self.binance.get_open_interest(symbol)
            ctx["current_price"]  = await self.binance.get_price(symbol)
        except Exception as e:
            log.debug("[E2] Market context partial error: %s", e)
        return ctx

    @staticmethod
    def _deduplicate(signals: List[RawSignal]) -> List[RawSignal]:
        best: Dict[str, RawSignal] = {}
        for sig in signals:
            key = f"{sig.symbol}_{sig.direction}"
            if key not in best or sig.confidence > best[key].confidence:
                best[key] = sig
        return list(best.values())

    async def _emit(self, sig: AnalyzedSignal) -> None:
        try:
            result = self.on_signal(sig)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.error("[E2] emit error: %s", e, exc_info=True)

    async def close(self) -> None:
        await self.binance.close()
