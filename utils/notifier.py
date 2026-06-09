"""
Multi-channel notifier — Telegram bot + Discord webhook.
"""

import aiohttp
from config import (NOTIFY_TELEGRAM_BOT_TOKEN, NOTIFY_TELEGRAM_CHAT_ID,
                    NOTIFY_DISCORD_WEBHOOK)
from utils.logger import get_logger

log = get_logger("Notifier")


async def _tg(text: str) -> None:
    if not (NOTIFY_TELEGRAM_BOT_TOKEN and NOTIFY_TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{NOTIFY_TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": NOTIFY_TELEGRAM_CHAT_ID,
               "text": text, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload) as r:
                if r.status != 200:
                    log.warning("TG notify failed: %s", await r.text())
    except Exception as e:
        log.error("TG notify error: %s", e)


async def _discord(text: str) -> None:
    if not NOTIFY_DISCORD_WEBHOOK:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(NOTIFY_DISCORD_WEBHOOK, json={"content": text[:2000]})
    except Exception as e:
        log.error("Discord notify error: %s", e)


async def notify(text: str) -> None:
    """Send to all configured channels."""
    log.info("NOTIFY: %s", text[:120])
    await _tg(text)
    await _discord(text)


async def notify_signal(sig) -> None:
    tps = " | ".join(f"TP{i+1}: {p:.4f}"
                     for i, p in enumerate(sig.take_profits))
    msg = (
        f"📡 <b>NEW SIGNAL — {sig.symbol}</b>\n"
        f"Direction : {sig.direction}\n"
        f"Entry     : {sig.entry_price:.4f}\n"
        f"Stop Loss : {sig.stop_loss:.4f}\n"
        f"{tps}\n"
        f"Confidence: {sig.overall_confidence:.1%}"
    )
    await notify(msg)


async def notify_trade_opened(order) -> None:
    msg = (
        f"🚀 <b>TRADE OPENED — {order.symbol}</b>\n"
        f"Direction : {order.direction}\n"
        f"Entry     : {order.entry_price:.4f}\n"
        f"Qty       : {order.quantity:.6f}\n"
        f"Leverage  : {order.leverage}×\n"
        f"SL        : {order.stop_loss:.4f}\n"
        f"Upgrades  : {', '.join(order.upgrades_applied) or 'none'}"
    )
    await notify(msg)


async def notify_trade_closed(symbol: str, pnl_usdt: float,
                               pnl_pct: float) -> None:
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    msg = (
        f"{emoji} <b>TRADE CLOSED — {symbol}</b>\n"
        f"PnL : {pnl_usdt:+.2f} USDT  ({pnl_pct:+.2f}%)"
    )
    await notify(msg)


async def notify_circuit_breaker(reason: str) -> None:
    await notify(f"🛑 <b>CIRCUIT BREAKER TRIGGERED</b>\n{reason}")
