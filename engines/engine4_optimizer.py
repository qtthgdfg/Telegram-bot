"""
╔══════════════════════════════════════════════════════════════════╗
║  ENGINE 4 — Trade Optimizer & Execution Engine                   ║
║  • Reviews RiskAssessedSignal from Engine 3                      ║
║  • Applies ML-style upgrades before execution                    ║
║  • Order book liquidity analysis                                 ║
║  • Smart entry (LIMIT vs MARKET vs scaled)                       ║
║  • Slippage estimation & adjustment                              ║
║  • Trailing stop upgrade                                         ║
║  • Iceberg order splitting for large positions                   ║
║  • Final execution on Binance Futures                            ║
║  • SL + multi-TP OCO ladder placement                            ║
║  • Position monitor loop (SL trail, partial TP)                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable

from config import (
    E4_SLIPPAGE_EST_PCT, E4_ORDER_BOOK_DEPTH,
    E4_ICEBERG_THRESHOLD_USDT, E4_MIN_UPGRADE_CONFIDENCE,
    E4_ENABLE_TRAILING_STOP, E4_TRAILING_STOP_PCT,
)
from models.signal import RiskAssessedSignal, OptimizedOrder
from models.trade import TradeRecord
from utils.binance_client import BinanceClient
from utils.database import save_trade, update_trade_status, get_open_trades
from utils.notifier import notify_trade_opened, notify_trade_closed
from utils.logger import get_logger

log = get_logger("Engine4-Optimizer")


# ══════════════════════════════════════════════════════════════════
# UPGRADE REGISTRY
# Each upgrade is a function:  (order, context) → (order, label | None)
# Return None label = upgrade not applicable
# ══════════════════════════════════════════════════════════════════

class Upgrades:

    @staticmethod
    def limit_entry(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Switch to LIMIT order if spread is tight."""
        spread_pct = ctx.get("spread_pct", 1.0)
        if spread_pct < 0.05 and order.order_type == "MARKET":
            order.order_type = "LIMIT"
            # price slightly inside spread for faster fill
            if order.direction == "LONG":
                order.entry_price = round(ctx["best_ask"] * 0.9999, 8)
            else:
                order.entry_price = round(ctx["best_bid"] * 1.0001, 8)
            return order, "LimitEntryUpgrade"
        return order, None

    @staticmethod
    def slippage_adjustment(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Widen entry slightly to improve fill probability."""
        adj = order.entry_price * E4_SLIPPAGE_EST_PCT / 100
        if order.direction == "LONG":
            order.entry_price += adj
        else:
            order.entry_price -= adj
        return order, "SlippageAdjust"

    @staticmethod
    def trailing_stop(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Add trailing stop if enabled and confidence is high enough."""
        if (E4_ENABLE_TRAILING_STOP and
                order.final_confidence >= E4_MIN_UPGRADE_CONFIDENCE):
            order.trailing_stop_pct = E4_TRAILING_STOP_PCT
            return order, "TrailingStop"
        return order, None

    @staticmethod
    def iceberg_split(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Split into iceberg if order is large."""
        if order.quantity * order.entry_price > E4_ICEBERG_THRESHOLD_USDT:
            order.order_type  = "ICEBERG"
            order.iceberg_qty = round(order.quantity / 5, 6)   # 20% slices
            return order, "IcebergSplit"
        return order, None

    @staticmethod
    def tp_partial_close(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Ensure at least 3 take-profit levels exist."""
        if len(order.take_profits) < 3:
            entry = order.entry_price
            sl_dist = abs(entry - order.risk_signal.stop_loss)
            rr = order.risk_signal.rr_ratio or 2.0
            if order.direction == "LONG":
                while len(order.take_profits) < 3:
                    i = len(order.take_profits) + 1
                    order.take_profits.append(round(entry + sl_dist * rr * i, 8))
            else:
                while len(order.take_profits) < 3:
                    i = len(order.take_profits) + 1
                    order.take_profits.append(round(entry - sl_dist * rr * i, 8))
            return order, "TPLadderUpgrade"
        return order, None

    @staticmethod
    def breakeven_stop(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Tag order for breakeven SL move after TP1."""
        order.risk_signal.risk_metadata["breakeven_on_tp1"] = True
        return order, "BreakevenOnTP1"

    @staticmethod
    def confidence_size_boost(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Boost size by up to 20% for very high-confidence setups."""
        if order.final_confidence >= 0.85:
            order.quantity = round(order.quantity * 1.15, 8)
            return order, "ConfidenceSizeBoost+15%"
        return order, None

    @staticmethod
    def low_confidence_size_cut(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Cut size by 40% for borderline signals."""
        if order.final_confidence < 0.65:
            order.quantity = round(order.quantity * 0.60, 8)
            return order, "LowConfSizeCut-40%"
        return order, None

    @staticmethod
    def time_in_force_upgrade(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Use IOC for scalp TFs to avoid resting orders."""
        tf = order.risk_signal.analyzed_signal.raw_signal.raw_metadata.get(
            "tf", "1h")
        if tf in ("1m", "5m") and order.order_type == "LIMIT":
            order.time_in_force = "IOC"
            return order, "IOCForScalpTF"
        return order, None

    @staticmethod
    def funding_rate_filter(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """If funding strongly opposes direction, reduce size."""
        fr = ctx.get("funding_rate", 0)
        if order.direction == "LONG" and fr > 0.002:
            order.quantity = round(order.quantity * 0.75, 8)
            return order, "FundingRateSizeCut"
        if order.direction == "SHORT" and fr < -0.002:
            order.quantity = round(order.quantity * 0.75, 8)
            return order, "FundingRateSizeCut"
        return order, None

    @staticmethod
    def open_interest_confirmation(order: "OptimizedOrder", ctx: Dict) -> tuple:
        """Block LONG if OI falling rapidly (longs liquidating)."""
        oi_trend = ctx.get("oi_trend", 0)
        if order.direction == "LONG" and oi_trend < -0.05:
            order.execute = False
            return order, "OITrendBlock"
        return order, None


ALL_UPGRADES = [
    Upgrades.limit_entry,
    Upgrades.slippage_adjustment,
    Upgrades.trailing_stop,
    Upgrades.iceberg_split,
    Upgrades.tp_partial_close,
    Upgrades.breakeven_stop,
    Upgrades.confidence_size_boost,
    Upgrades.low_confidence_size_cut,
    Upgrades.time_in_force_upgrade,
    Upgrades.funding_rate_filter,
    Upgrades.open_interest_confirmation,
]


# ══════════════════════════════════════════════════════════════════
# ENGINE 4
# ══════════════════════════════════════════════════════════════════

class Engine4Optimizer:

    def __init__(self):
        self.binance = BinanceClient()

    # ── Public entry ───────────────────────────────────────────────

    async def process(self, risk_sig: RiskAssessedSignal) -> None:
        if not risk_sig.approved:
            log.info("[E4] Skipping rejected signal: %s — %s",
                     risk_sig.symbol, risk_sig.rejection_reason)
            return
        try:
            order = await self._optimize(risk_sig)
            if order and order.execute:
                await self._execute(order)
            else:
                log.info("[E4] Execution blocked by optimiser for %s", risk_sig.symbol)
        except Exception as e:
            log.error("[E4] process error: %s", e, exc_info=True)

    # ══════════════════════════════════════════════════════════════
    # OPTIMISATION PIPELINE
    # ══════════════════════════════════════════════════════════════

    async def _optimize(self, risk: RiskAssessedSignal) -> Optional[OptimizedOrder]:
        sym = risk.symbol

        # Build market context
        ctx = await self._build_context(sym)

        # Initial order skeleton
        order = OptimizedOrder(
            risk_signal      = risk,
            symbol           = sym,
            direction        = risk.direction,
            order_type       = "MARKET",
            entry_price      = risk.entry_price,
            stop_loss        = risk.stop_loss,
            take_profits     = list(risk.take_profits),
            trailing_stop_pct= None,
            quantity         = risk.position_size_qty,
            leverage         = risk.leverage,
            time_in_force    = "GTC",
            iceberg_qty      = None,
            final_confidence = risk.analyzed_signal.overall_confidence,
            execute          = True,
        )

        # Run all upgrades
        applied: List[str] = []
        for upgrade_fn in ALL_UPGRADES:
            try:
                order, label = upgrade_fn(order, ctx)
                if label:
                    applied.append(label)
                    log.debug("[E4] Upgrade applied: %s", label)
            except Exception as e:
                log.warning("[E4] Upgrade error %s: %s", upgrade_fn.__name__, e)

        order.upgrades_applied  = applied
        order.execution_notes   = f"Upgrades: {', '.join(applied) or 'none'}"

        # Round to exchange precision
        try:
            order.quantity    = await self.binance.round_quantity(sym, order.quantity)
            order.entry_price = await self.binance.round_price(sym, order.entry_price)
            order.stop_loss   = await self.binance.round_price(sym, order.stop_loss)
            order.take_profits= [
                await self.binance.round_price(sym, tp)
                for tp in order.take_profits
            ]
        except Exception as e:
            log.warning("[E4] Precision rounding error: %s", e)

        if order.quantity <= 0:
            log.error("[E4] Zero quantity after rounding — aborting %s", sym)
            order.execute = False

        log.info(
            "[E4] 🛠  Optimised %s %s | type=%s qty=%.6f lev=%dx | %s",
            order.direction, sym, order.order_type,
            order.quantity, order.leverage,
            ", ".join(applied) or "no upgrades",
        )
        return order

    # ══════════════════════════════════════════════════════════════
    # EXECUTION
    # ══════════════════════════════════════════════════════════════

    async def _execute(self, order: OptimizedOrder) -> None:
        sym = order.symbol
        log.info("[E4] 🚀 EXECUTING %s %s", order.direction, sym)

        try:
            # 1. Set leverage & margin type
            await self.binance.set_leverage(sym, order.leverage)
            await self.binance.set_margin_type(sym, "ISOLATED")

            # 2. Entry order
            entry_side = "BUY" if order.direction == "LONG" else "SELL"
            if order.order_type == "MARKET":
                resp = await self.binance.place_market_order(
                    sym, entry_side, order.quantity)
            elif order.order_type == "ICEBERG":
                resp = await self.binance.place_limit_order(
                    sym, entry_side, order.quantity,
                    order.entry_price, order.time_in_force)
            else:  # LIMIT
                resp = await self.binance.place_limit_order(
                    sym, entry_side, order.quantity,
                    order.entry_price, order.time_in_force)

            binance_order_id = str(resp.get("orderId", ""))
            filled_price     = float(resp.get("avgPrice") or order.entry_price)
            log.info("[E4] Entry order placed: %s  id=%s  fillPrice=%.6f",
                     sym, binance_order_id, filled_price)

            # 3. Stop-loss order
            sl_side = "SELL" if order.direction == "LONG" else "BUY"
            if order.trailing_stop_pct:
                await self.binance.place_trailing_stop(
                    sym, sl_side, order.quantity, order.trailing_stop_pct)
                log.info("[E4] Trailing SL placed: callback=%.1f%%",
                         order.trailing_stop_pct)
            else:
                await self.binance.place_stop_market(
                    sym, sl_side, order.quantity, order.stop_loss)
                log.info("[E4] Fixed SL placed: %.6f", order.stop_loss)

            # 4. Take-profit ladder (distribute quantity equally)
            tp_side = "SELL" if order.direction == "LONG" else "BUY"
            tp_qty  = round(order.quantity / len(order.take_profits), 6)
            for i, tp in enumerate(order.take_profits):
                try:
                    await self.binance.place_take_profit_market(sym, tp_side, tp)
                    log.info("[E4] TP%d placed: %.6f", i+1, tp)
                    await asyncio.sleep(0.1)   # rate-limit guard
                except Exception as e:
                    log.warning("[E4] TP%d placement failed: %s", i+1, e)

            # 5. Persist trade record
            raw_sig = order.risk_signal.analyzed_signal.raw_signal
            trade = TradeRecord(
                symbol           = sym,
                direction        = order.direction,
                entry_price      = filled_price,
                quantity         = order.quantity,
                leverage         = order.leverage,
                stop_loss        = order.stop_loss,
                take_profits     = str(order.take_profits),
                order_type       = order.order_type,
                binance_order_id = binance_order_id,
                status           = "OPEN",
                confidence       = order.final_confidence,
                upgrades_applied = str(order.upgrades_applied),
                source_channel   = raw_sig.source_channel,
                signal_text      = raw_sig.message_text[:500],
            )
            save_trade(trade)

            # 6. Notify
            await notify_trade_opened(order)

            # 7. Start monitor loop
            asyncio.create_task(self._monitor_position(sym, order, filled_price))

        except Exception as e:
            log.error("[E4] Execution FAILED for %s: %s", sym, e, exc_info=True)

    # ══════════════════════════════════════════════════════════════
    # POSITION MONITOR
    # ══════════════════════════════════════════════════════════════

    async def _monitor_position(self, symbol: str,
                                 order: OptimizedOrder,
                                 fill_price: float) -> None:
        """
        Background loop:
        - Checks if position is still open every 30 s
        - Moves SL to breakeven after TP1 is hit
        - Detects position close and updates DB
        """
        log.info("[E4] Position monitor started for %s", symbol)
        tp1       = order.take_profits[0] if order.take_profits else None
        be_moved  = False
        entry     = fill_price
        direction = order.direction

        while True:
            await asyncio.sleep(30)
            try:
                positions = await self.binance.get_positions()
                pos = next((p for p in positions if p["symbol"] == symbol), None)

                if pos is None or float(pos.get("positionAmt", 0)) == 0:
                    # Position closed
                    log.info("[E4] Position %s closed — recording", symbol)
                    # Try to get PnL from recent trades
                    await self._record_close(symbol, entry, direction)
                    break

                current_price = float(pos.get("markPrice", entry))
                upnl_pct = float(pos.get("percentage", 0))

                # Move SL to breakeven after TP1
                if tp1 and not be_moved:
                    hit_tp1 = (
                        (direction == "LONG"  and current_price >= tp1) or
                        (direction == "SHORT" and current_price <= tp1)
                    )
                    if hit_tp1 and order.risk_signal.risk_metadata.get("breakeven_on_tp1"):
                        sl_side = "SELL" if direction == "LONG" else "BUY"
                        try:
                            # Cancel old SL and place new at entry
                            await self.binance.cancel_all_orders(symbol)
                            await self.binance.place_stop_market(
                                symbol, sl_side, order.quantity, entry)
                            be_moved = True
                            log.info("[E4] 🔄 SL moved to breakeven for %s", symbol)
                        except Exception as e:
                            log.warning("[E4] Breakeven SL error: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[E4] Monitor loop error %s: %s", symbol, e)
                await asyncio.sleep(60)

    async def _record_close(self, symbol: str,
                             entry: float, direction: str) -> None:
        try:
            price  = await self.binance.get_price(symbol)
            pnl_pct = ((price - entry)/entry*100
                        if direction == "LONG"
                        else (entry - price)/entry*100)
            # Find most recent OPEN trade for this symbol
            open_ts = get_open_trades()
            for t in open_ts:
                if t["symbol"] == symbol:
                    qty = t["quantity"] * entry
                    pnl_usdt = qty * pnl_pct / 100
                    update_trade_status(t["id"], "CLOSED", price,
                                        round(pnl_usdt, 4), round(pnl_pct, 4))
                    await notify_trade_closed(symbol, pnl_usdt, pnl_pct)
                    log.info("[E4] Trade closed %s PnL=%.2f USDT (%.2f%%)",
                             symbol, pnl_usdt, pnl_pct)
                    break
        except Exception as e:
            log.error("[E4] Record close error: %s", e)

    # ══════════════════════════════════════════════════════════════
    # CONTEXT BUILDER
    # ══════════════════════════════════════════════════════════════

    async def _build_context(self, symbol: str) -> Dict:
        ctx: Dict[str, Any] = {}
        try:
            ob = await self.binance.get_order_book(symbol, E4_ORDER_BOOK_DEPTH)
            best_bid = float(ob["bids"][0][0]) if ob.get("bids") else 0
            best_ask = float(ob["asks"][0][0]) if ob.get("asks") else 0
            spread   = (best_ask - best_bid) / best_ask * 100 if best_ask else 1
            ctx.update({"best_bid": best_bid, "best_ask": best_ask,
                        "spread_pct": spread})

            ctx["funding_rate"] = await self.binance.get_funding_rate(symbol)
            ctx["open_interest"]= await self.binance.get_open_interest(symbol)

            # OI trend: compare current vs 5 min ago (approximate with a second fetch)
            ctx["oi_trend"] = 0   # placeholder; full trend needs time-series OI

        except Exception as e:
            log.debug("[E4] Context partial error %s: %s", symbol, e)
        return ctx

    async def close(self) -> None:
        await self.binance.close()
