

import asyncio
from datetime import datetime
from typing import List, Callable, Any, Optional

from config import (
    E3_MAX_ACCOUNT_RISK_PCT, E3_MAX_PORTFOLIO_EXPOSURE,
    E3_MAX_SINGLE_POSITION_PCT, E3_MAX_DAILY_LOSS_PCT,
    E3_MAX_DRAWDOWN_PCT, E3_DEFAULT_RR_RATIO,
    E3_ATR_MULTIPLIER_SL, E3_ATR_MULTIPLIER_TP,
    E3_MAX_OPEN_TRADES, E3_KELLY_FRACTION,
)
from models.signal import AnalyzedSignal, RiskAssessedSignal
from models.trade import AccountSnapshot
from utils.binance_client import BinanceClient
from utils.database import get_open_trades, get_daily_pnl, save_account_snapshot
from utils.notifier import notify_circuit_breaker
from utils.logger import get_logger

log = get_logger("Engine3-Risk")


# ══════════════════════════════════════════════════════════════════
# VOLATILITY REGIME  →  leverage cap
# ══════════════════════════════════════════════════════════════════

def _volatility_leverage_cap(atr_pct: float, requested: int) -> int:
    """
    atr_pct = ATR / price  (fraction, not %)
    Higher volatility → lower max leverage.
    """
    if   atr_pct > 0.05: cap = 3
    elif atr_pct > 0.03: cap = 5
    elif atr_pct > 0.02: cap = 10
    elif atr_pct > 0.01: cap = 20
    elif atr_pct > 0.005: cap = 50
    else:                cap = 75
    return min(requested, cap)


# ══════════════════════════════════════════════════════════════════
# KELLY CRITERION
# ══════════════════════════════════════════════════════════════════

def _kelly_fraction(win_prob: float, rr: float) -> float:
    """
    Kelly = W - (1-W)/R  where W=win_prob, R=reward:risk
    Returns the fraction of account to risk (fractional Kelly applied).
    """
    if rr <= 0 or win_prob <= 0:
        return 0.005
    kelly = win_prob - (1 - win_prob) / rr
    kelly = max(0.0, kelly)
    return round(kelly * E3_KELLY_FRACTION, 5)


# ══════════════════════════════════════════════════════════════════
# ACCOUNT SNAPSHOT BUILDER
# ══════════════════════════════════════════════════════════════════

async def _build_account_snapshot(binance: BinanceClient) -> AccountSnapshot:
    try:
        acc   = await binance.get_account()
        usdt  = next((a for a in acc.get("assets", []) if a["asset"] == "USDT"), {})
        wallet    = float(usdt.get("walletBalance", 0))
        available = float(usdt.get("availableBalance", 0))
        upnl      = float(acc.get("totalUnrealizedProfit", 0))
        positions = [p for p in acc.get("positions", [])
                     if float(p.get("positionAmt", 0)) != 0]
        margin    = sum(float(p.get("initialMargin", 0)) for p in positions)

        daily_pnl_usdt = get_daily_pnl()
        daily_pnl_pct  = (daily_pnl_usdt / wallet * 100) if wallet else 0

        # Peak for drawdown — approximate from wallet
        peak = wallet - upnl           # treat current unrealised as the swing
        drawdown_pct = (peak - wallet) / peak * 100 if peak > 0 else 0
        drawdown_pct = max(0.0, drawdown_pct)

        snap = AccountSnapshot(
            total_wallet_balance  = wallet,
            available_balance     = available,
            total_unrealized_pnl  = upnl,
            total_margin_used     = margin,
            open_positions        = len(positions),
            daily_pnl_usdt        = daily_pnl_usdt,
            daily_pnl_pct         = daily_pnl_pct,
            max_drawdown_pct      = drawdown_pct,
        )
        try:
            save_account_snapshot(snap)
        except Exception:
            pass
        return snap

    except Exception as e:
        log.error("[E3] Account snapshot error: %s", e)
        return AccountSnapshot(0, 0, 0, 0, 0, 0, 0, 0)


# ══════════════════════════════════════════════════════════════════
# ENGINE 3
# ══════════════════════════════════════════════════════════════════

class Engine3Risk:
    """
    Receives AnalyzedSignal from Engine 2.
    Applies all risk rules and emits RiskAssessedSignal → Engine 4.
    """

    def __init__(self, on_signal: Callable[[RiskAssessedSignal], Any]):
        self.on_signal      = on_signal
        self.binance        = BinanceClient()
        self._circuit_open  = False
        self._circuit_reason= ""

    # ── Public entry ───────────────────────────────────────────────

    async def process(self, sig: AnalyzedSignal) -> None:
        try:
            assessed = await self._assess(sig)
            if assessed:
                await self._emit(assessed)
        except Exception as e:
            log.error("[E3] process error: %s", e, exc_info=True)

    # ══════════════════════════════════════════════════════════════
    # MAIN ASSESSMENT PIPELINE
    # ══════════════════════════════════════════════════════════════

    async def _assess(self, sig: AnalyzedSignal) -> Optional[RiskAssessedSignal]:

        # ── A.  Account snapshot ───────────────────────────────────
        snap = await _build_account_snapshot(self.binance)
        wallet    = snap.total_wallet_balance
        available = snap.available_balance

        if wallet < 10:
            return self._reject(sig, "Account balance too low (< 10 USDT)")

        # ── B.  Circuit breakers ───────────────────────────────────
        cb_reason = self._check_circuit_breakers(snap)
        if cb_reason:
            log.warning("[E3] CIRCUIT BREAKER: %s", cb_reason)
            await notify_circuit_breaker(cb_reason)
            return self._reject(sig, f"Circuit breaker: {cb_reason}",
                                circuit_breaker=True)

        # ── C.  Max open trades ────────────────────────────────────
        open_trades = get_open_trades()
        if len(open_trades) >= E3_MAX_OPEN_TRADES:
            return self._reject(sig,
                f"Max open trades reached ({E3_MAX_OPEN_TRADES})")

        # ── D.  Duplicate symbol check ─────────────────────────────
        open_symbols = [t["symbol"] for t in open_trades]
        if sig.symbol in open_symbols:
            return self._reject(sig,
                f"Already have open trade on {sig.symbol}")

        # ── E.  ATR & price validation ─────────────────────────────
        atr_1h = sig.indicator_snapshot.get("1h", {}).get("atr") or \
                 sig.indicator_snapshot.get("4h", {}).get("atr") or \
                 sig.entry_price * 0.01
        atr_pct = atr_1h / sig.entry_price if sig.entry_price else 0.01

        # ── F.  Validate SL makes sense ────────────────────────────
        sl = sig.stop_loss
        entry = sig.entry_price

        if sig.direction == "LONG":
            if sl >= entry:
                sl = entry - E3_ATR_MULTIPLIER_SL * atr_1h
            max_sl_dist = entry * 0.10    # SL can't be more than 10% away
            if (entry - sl) > max_sl_dist:
                sl = entry - max_sl_dist
        else:
            if sl <= entry:
                sl = entry + E3_ATR_MULTIPLIER_SL * atr_1h
            max_sl_dist = entry * 0.10
            if (sl - entry) > max_sl_dist:
                sl = entry + max_sl_dist

        sl_dist_pct = abs(entry - sl) / entry

        # ── G.  R:R check ──────────────────────────────────────────
        if sig.take_profits:
            tp1 = sig.take_profits[0]
            sl_dist  = abs(entry - sl)
            tp_dist  = abs(tp1 - entry)
            rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
        else:
            rr_ratio = 0

        if rr_ratio < E3_DEFAULT_RR_RATIO:
            return self._reject(sig,
                f"R:R {rr_ratio:.2f} < minimum {E3_DEFAULT_RR_RATIO}")

        # ── H.  Leverage cap via volatility ────────────────────────
        requested_lev = sig.raw_signal.leverage
        leverage      = _volatility_leverage_cap(atr_pct, requested_lev)
        if leverage != requested_lev:
            log.info("[E3] Leverage capped %d→%d (ATR %.2f%%)",
                     requested_lev, leverage, atr_pct*100)

        # ── I.  Position sizing ────────────────────────────────────
        # Use Kelly Criterion to determine risk fraction
        win_prob  = sig.overall_confidence          # proxy for win probability
        kelly_f   = _kelly_fraction(win_prob, rr_ratio)

        # Cap to configured max
        risk_pct  = min(kelly_f * 100, E3_MAX_ACCOUNT_RISK_PCT)

        # Risk amount in USDT
        risk_usdt = wallet * risk_pct / 100

        # Position size: risk_usdt / (sl_dist_pct × entry)
        # With leverage: we risk sl_dist_pct of the notional, so
        # position_notional = risk_usdt / sl_dist_pct
        if sl_dist_pct <= 0:
            return self._reject(sig, "SL distance is zero — cannot size")

        position_notional = risk_usdt / sl_dist_pct   # USDT notional
        margin_required   = position_notional / leverage

        # ── J.  Cap to max single position % ──────────────────────
        max_pos = wallet * E3_MAX_SINGLE_POSITION_PCT / 100
        if margin_required > max_pos:
            margin_required   = max_pos
            position_notional = margin_required * leverage
            risk_usdt         = position_notional * sl_dist_pct

        # ── K.  Cap to max portfolio exposure ─────────────────────
        current_exposure = snap.total_margin_used
        remaining_budget = wallet * E3_MAX_PORTFOLIO_EXPOSURE / 100 - current_exposure
        if margin_required > remaining_budget:
            if remaining_budget <= 0:
                return self._reject(sig, "Portfolio exposure limit reached")
            margin_required   = remaining_budget
            position_notional = margin_required * leverage
            risk_usdt         = position_notional * sl_dist_pct

        # ── L.  Available balance check ────────────────────────────
        if margin_required > available * 0.95:
            return self._reject(sig,
                f"Insufficient available balance ({available:.2f} USDT)")

        # ── M.  Compute quantity ───────────────────────────────────
        quantity = position_notional / entry

        # ── N.  Final take-profit recalculation ────────────────────
        sl_dist  = abs(entry - sl)
        if sig.direction == "LONG":
            tps = [
                entry + sl_dist * rr_ratio,
                entry + sl_dist * rr_ratio * 1.5,
                entry + sl_dist * rr_ratio * 2.0,
            ]
        else:
            tps = [
                entry - sl_dist * rr_ratio,
                entry - sl_dist * rr_ratio * 1.5,
                entry - sl_dist * rr_ratio * 2.0,
            ]
        # Use original TPs if they are in the right direction and better
        if sig.take_profits:
            original_tps = sig.take_profits
            if sig.direction == "LONG" and all(t > entry for t in original_tps):
                tps = original_tps
            if sig.direction == "SHORT" and all(t < entry for t in original_tps):
                tps = original_tps

        log.info(
            "[E3] ✅ APPROVED %s %s | entry=%.4f sl=%.4f tp1=%.4f | "
            "qty=%.4f lev=%dx risk=%.2f USDT (%.2f%%) RR=%.2f",
            sig.symbol, sig.direction, entry, sl, tps[0] if tps else 0,
            quantity, leverage, risk_usdt, risk_pct, rr_ratio,
        )

        return RiskAssessedSignal(
            analyzed_signal    = sig,
            symbol             = sig.symbol,
            direction          = sig.direction,
            entry_price        = round(entry, 8),
            stop_loss          = round(sl, 8),
            take_profits       = [round(t, 8) for t in tps],
            position_size_usdt = round(position_notional, 4),
            position_size_qty  = round(quantity, 8),
            risk_amount_usdt   = round(risk_usdt, 4),
            rr_ratio           = round(rr_ratio, 3),
            leverage           = leverage,
            account_risk_pct   = round(risk_pct, 4),
            approved           = True,
            risk_metadata      = {
                "wallet_balance"   : wallet,
                "available_balance": available,
                "kelly_f"          : kelly_f,
                "atr_pct"          : round(atr_pct * 100, 3),
                "sl_dist_pct"      : round(sl_dist_pct * 100, 3),
                "open_trades"      : len(open_trades),
                "daily_pnl_pct"    : snap.daily_pnl_pct,
            },
        )

    # ══════════════════════════════════════════════════════════════
    # CIRCUIT BREAKERS
    # ══════════════════════════════════════════════════════════════

    def _check_circuit_breakers(self, snap: AccountSnapshot) -> Optional[str]:
        # Daily loss
        if snap.daily_pnl_pct < -E3_MAX_DAILY_LOSS_PCT:
            return (f"Daily loss limit hit: {snap.daily_pnl_pct:.2f}% "
                    f"(limit -{E3_MAX_DAILY_LOSS_PCT}%)")
        # Max drawdown
        if snap.max_drawdown_pct > E3_MAX_DRAWDOWN_PCT:
            return (f"Max drawdown hit: {snap.max_drawdown_pct:.2f}% "
                    f"(limit {E3_MAX_DRAWDOWN_PCT}%)")
        # Zero balance
        if snap.total_wallet_balance <= 0:
            return "Zero wallet balance"
        return None

    # ══════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _reject(sig: AnalyzedSignal, reason: str,
                circuit_breaker: bool = False) -> RiskAssessedSignal:
        log.info("[E3] ❌ REJECTED %s %s — %s", sig.symbol, sig.direction, reason)
        return RiskAssessedSignal(
            analyzed_signal    = sig,
            symbol             = sig.symbol,
            direction          = sig.direction,
            entry_price        = sig.entry_price,
            stop_loss          = sig.stop_loss,
            take_profits       = sig.take_profits,
            position_size_usdt = 0,
            position_size_qty  = 0,
            risk_amount_usdt   = 0,
            rr_ratio           = 0,
            leverage           = 1,
            account_risk_pct   = 0,
            approved           = False,
            rejection_reason   = reason,
            circuit_breaker    = circuit_breaker,
        )

    async def _emit(self, sig: RiskAssessedSignal) -> None:
        try:
            result = self.on_signal(sig)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.error("[E3] emit error: %s", e, exc_info=True)

    async def close(self) -> None:
        await self.binance.close()
