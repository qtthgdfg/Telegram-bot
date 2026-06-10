

import asyncio
import os
import sys
import signal
from typing import List

from config import (
    TELEGRAM_API_ID, BINANCE_API_KEY,
    BINANCE_TESTNET, LOG_FILE,
)
from engines.engine1_telegram import Engine1Telegram
from engines.engine2_analyzer  import Engine2Analyzer
from engines.engine3_risk       import Engine3Risk
from engines.engine4_optimizer  import Engine4Optimizer
from utils.database  import init_db
from utils.logger    import get_logger
from utils.notifier  import notify
from models.signal   import RawSignal, AnalyzedSignal, RiskAssessedSignal
# ═══ NEW: State management ═══
from utils.state_manager import bot_state
log = get_logger("Main")

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
HISTORY_LIMIT = int(os.getenv("TELEGRAM_HISTORY_LIMIT", 5000))

# ══════════════════════════════════════════════════════════════════
# PIPELINE ASSEMBLY
# ══════════════════════════════════════════════════════════════════

class CryptoSignalBot:

    def __init__(self):
        # Wire engines back-to-front so each callback exists at init time
        self.engine4 = Engine4Optimizer()
        self.engine3 = Engine3Risk(on_signal=self._on_risk_signal)
        self.engine2 = Engine2Analyzer(on_signal=self._on_analyzed_signal)
        self.engine1 = Engine1Telegram(
            on_signals=self._on_raw_signals,
            history_limit=HISTORY_LIMIT,
            last_message_id=bot_state.get_last_message_id()
        )  
    # ── Engine 1 → Engine 2 ────────────────────────────────────────

    async def _on_raw_signals(self, raw_signals: List[RawSignal]) -> None:
    # ═══ NEW: Filter already-processed signals ═══
    new_signals = []
    for sig in raw_signals:
        if not bot_state.is_signal_processed({"id": sig.message_id}):
            new_signals.append(sig)
            bot_state.add_signal({"id": sig.message_id})
    
    if not new_signals:
        log.info("📩 No new signals to process")
        return
    
    log.info("📩 Engine1 → Engine2: %d new signals (skipped %d duplicates)",
             len(new_signals), len(raw_signals) - len(new_signals))
    await self.engine2.process(new_signals)
    
    # ── Engine 2 → Engine 3 ────────────────────────────────────────

    async def _on_analyzed_signal(self, sig: AnalyzedSignal) -> None:
        log.info("📊 Engine2 → Engine3: %s %s  conf=%.3f",
                 sig.symbol, sig.direction, sig.overall_confidence)
        await self.engine3.process(sig)

    # ── Engine 3 → Engine 4 ────────────────────────────────────────

    async def _on_risk_signal(self, sig: RiskAssessedSignal) -> None:
        if not sig.approved:
            log.info("🚫 Engine3 rejected: %s — %s",
                     sig.symbol, sig.rejection_reason)
            return
        log.info("✅ Engine3 → Engine4: %s %s  qty=%.4f  lev=%dx",
                 sig.symbol, sig.direction, sig.position_size_qty, sig.leverage)

        # ═══ NEW: Track trade ═══
        bot_state.record_trade(success=True)

        if DRY_RUN:
            log.info("🧪 DRY RUN — skipping live execution for %s", sig.symbol)
            _log_dry_run(sig)
            return

        await self.engine4.process(sig)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        log.info("=" * 60)
        log.info(" CRYPTO SIGNAL BOT STARTING")
        log.info(" Run #%d | History Limit: %d | First Run: %s",
                 bot_state.state["runs_count"] + 1,
                 HISTORY_LIMIT,
                 bot_state.state["first_run"])
        log.info("=" * 60)
    
        _check_config()
        init_db()

        await notify(
            f🤖 <b>Crypto Signal Bot started</b>\n"
            f"Run: #{bot_state.state['runs_count'] + 1}\n"
            f"Mode: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE TRADING'}\n"
            f"Testnet: {BINANCE_TESTNET}\n"
            f"History Limit: {HISTORY_LIMIT}"
        )
        
        # Engine1 is the event driver — blocks until Telegram disconnects
        await self.engine1.start()
        
    async def stop(self) -> None:
        log.info("Bot shutting down …")
    
        # ═══ NEW: Save state before stopping ═══
        log.info(bot_state.get_summary())
        bot_state.save_state()
    
        await self.engine1.stop()
        await self.engine2.close()
        await self.engine3.close()
        await self.engine4.close()
        await notify("🛑 Crypto Signal Bot stopped\n" + bot_state.get_summary())
    

# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _check_config() -> None:
    errors = []
    if not TELEGRAM_API_ID or TELEGRAM_API_ID == 0:
        errors.append("TELEGRAM_API_ID not set")
    if not os.getenv("TELEGRAM_API_HASH"):
        errors.append("TELEGRAM_API_HASH not set")
    if not BINANCE_API_KEY:
        errors.append("BINANCE_API_KEY not set")
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        if not DRY_RUN:
            log.error("Fix config errors before running in LIVE mode.")
            sys.exit(1)
        else:
            log.warning("Running in DRY_RUN mode with incomplete config.")


def _log_dry_run(sig: RiskAssessedSignal) -> None:
    tps = " | ".join(f"TP{i+1}: {p:.4f}"
                     for i, p in enumerate(sig.take_profits))
    log.info(
        "\n╔═ DRY RUN TRADE ════════════════════════════════╗\n"
        "  Symbol   : %s\n"
        "  Direction: %s\n"
        "  Entry    : %.4f\n"
        "  Stop-Loss: %.4f\n"
        "  %s\n"
        "  Quantity : %.6f\n"
        "  Leverage : %dx\n"
        "  Risk     : %.2f USDT (%.2f%%)\n"
        "  R:R      : %.2f\n"
        "  Confidence: %.2f%%\n"
        "╚════════════════════════════════════════════════╝",
        sig.symbol, sig.direction, sig.entry_price, sig.stop_loss, tps,
        sig.position_size_qty, sig.leverage,
        sig.risk_amount_usdt, sig.account_risk_pct,
        sig.rr_ratio,
        sig.analyzed_signal.overall_confidence * 100,
    )


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

async def _main() -> None:
    bot = CryptoSignalBot()

    loop = asyncio.get_event_loop()

    def _sig_handler():
        asyncio.create_task(bot.stop())

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig_handler)
        except NotImplementedError:
            pass  # Windows

    await bot.start()
    

if __name__ == "__main__":
    import os
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # ═══ NEW: Show state on startup ═══
    log.info("State summary: %s", bot_state.get_summary())
    
    asyncio.run(_main())
