"""
╔══════════════════════════════════════════════════════════════════╗
║  ENGINE 1 — Telegram Intelligence Engine                         ║
║  • Connects to Telegram via Telethon                             ║
║  • Scans all historical + live messages                          ║
║  • NLP signal extraction (symbol, direction, entry, SL, TPs)    ║
║  • Sentiment scoring + confidence weighting                      ║
║  • Passes structured RawSignal list → Engine 2                   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import re
import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Dict, Callable, Any

from telethon import TelegramClient, events
from telethon.tl.types import Message

from config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE,
    TELEGRAM_SESSION_FILE, TELEGRAM_CHANNELS,
    TELEGRAM_HISTORY_LIMIT, E1_SCAN_INTERVAL_SEC,
    E1_MIN_CONFIDENCE, E1_SENTIMENT_WINDOW,
)
from models.signal import RawSignal
from utils.logger import get_logger
from utils.database import save_raw_signal

log = get_logger("Engine1-Telegram")


# ══════════════════════════════════════════════════════════════════
# KNOWN CRYPTO SYMBOLS (top 200+)
# ══════════════════════════════════════════════════════════════════

KNOWN_SYMBOLS = {
    "BTC","ETH","BNB","SOL","XRP","ADA","DOGE","AVAX","DOT","MATIC",
    "LINK","UNI","LTC","ATOM","XLM","ETC","NEAR","APT","OP","ARB",
    "MANA","SAND","AXS","FTM","HBAR","VET","ICP","FIL","AAVE","MKR",
    "CRV","LDO","RPL","SUSHI","COMP","SNX","YFI","BAL","UMA","1INCH",
    "ZRX","ENS","IMX","GALA","CHZ","FLOW","EOS","ALGO","EGLD","KAVA",
    "THETA","GRT","IOTA","NEO","WAVES","ZEC","DASH","XMR","BCH","BSV",
    "TRX","XTZ","KSM","RUNE","INJ","SUI","SEI","TIA","BLUR","PEPE",
    "FLOKI","SHIB","BONE","LUNC","LUNA","UST","ROSE","CFX","MAGIC",
    "GMX","GNS","DYDX","PERP","RDNT","WLD","PYTH","JTO","MEME","ORDI",
    "SATS","RATS","BOME","WIF","BONK","JUP","TNSR","STRK","ETHFI",
    "REZ","BB","NOT","IO","ZK","LISTA","ZRO","BANANA","DOGS","HMSTR",
    "CATI","MAJOR","1000SHIB","1000PEPE","1000BONK","1000FLOKI",
}

QUOTE_CURRENCIES = {"USDT","USDC","BUSD","BTC","ETH","BNB"}


# ══════════════════════════════════════════════════════════════════
# SIGNAL EXTRACTION PATTERNS
# ══════════════════════════════════════════════════════════════════

# Direction
RE_LONG  = re.compile(
    r'\b(long|buy|bull|bullish|call|upside|pump|breakout above|entry long)\b', re.I)
RE_SHORT = re.compile(
    r'\b(short|sell|bear|bearish|put|downside|dump|breakdown|entry short)\b', re.I)

# Symbol  e.g. $BTC  BTCUSDT  BTC/USDT  #ETHUSDT  BTC-USDT
RE_SYMBOL = re.compile(
    r'[\$#]?([A-Z]{2,12})(?:[/\-_]?(USDT|USDC|BUSD|BTC|ETH|BNB))?', re.I)

# Price levels
RE_ENTRY = re.compile(
    r'(?:entry|enter|buy\s*(?:at|@|zone)?|price|open|trigger)[:\s@]*'
    r'(\d[\d,\.]+(?:\s*[-–]\s*\d[\d,\.]+)?)', re.I)
RE_SL    = re.compile(
    r'(?:sl|stop[\s\-_]?loss|invalidation|stop)[:\s@]*(\d[\d,\.]+)', re.I)
RE_TP    = re.compile(
    r'(?:tp\s*\d?|take[\s\-_]?profit\s*\d?|target\s*\d?)[:\s@]*(\d[\d,\.]+)', re.I)
RE_LEV   = re.compile(r'(?:lev(?:erage)?|x)\s*[:\s]*(\d+)\s*[xX]?', re.I)

# Sentiment lexicons
BULLISH_WORDS = [
    "bullish","buy","long","accumulate","upside","breakout","support",
    "bounce","recovery","green","pump","moon","ath","strong","hold",
    "dip buy","opportunity","reversal up","golden cross","oversold",
]
BEARISH_WORDS = [
    "bearish","sell","short","dump","downside","breakdown","resistance",
    "fall","drop","red","crash","overbought","death cross","retest fail",
    "reject","weak","distribution","rug",
]
NEUTRAL_WORDS = ["wait","sideways","range","consolidat","watch","monitor"]


def _clean_price(raw: str) -> Optional[float]:
    try:
        val = raw.strip().replace(",", "").split("-")[0].split("–")[0].strip()
        return float(val)
    except Exception:
        return None


def _extract_symbol(text: str) -> Optional[str]:
    """Extract and normalise crypto symbol from free text."""
    text_upper = text.upper()

    # Direct XXXUSDT or XXX/USDT pattern
    direct = re.findall(r'\b([A-Z]{2,10})(?:USDT|USDC|BUSD)\b', text_upper)
    if direct:
        return direct[0] + "USDT"

    # $XXX or #XXX
    tagged = re.findall(r'[\$#]([A-Z]{2,10})', text_upper)
    for t in tagged:
        if t in KNOWN_SYMBOLS:
            return t + "USDT"

    # standalone known symbol word
    words = re.findall(r'\b([A-Z]{2,10})\b', text_upper)
    for w in words:
        if w in KNOWN_SYMBOLS:
            return w + "USDT"

    return None


def _extract_take_profits(text: str) -> List[float]:
    tps = []
    for m in RE_TP.finditer(text):
        v = _clean_price(m.group(1))
        if v and v not in tps:
            tps.append(v)

    # fallback: look for numbered targets  "T1: 45000  T2: 48000"
    if not tps:
        numbered = re.findall(
            r'(?:t|tp|target|take\s*profit)\s*\d\s*[:\-@]\s*(\d[\d,\.]+)', text, re.I)
        for n in numbered:
            v = _clean_price(n)
            if v:
                tps.append(v)
    return sorted(tps)


def _sentiment_score(text: str) -> float:
    """Return -1 … +1 sentiment score."""
    words = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in words)
    bear = sum(1 for w in BEARISH_WORDS if w in words)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 3)


def _direction_from_text(text: str) -> str:
    long_count  = len(RE_LONG.findall(text))
    short_count = len(RE_SHORT.findall(text))
    if long_count > short_count:
        return "LONG"
    if short_count > long_count:
        return "SHORT"
    # fallback from sentiment
    score = _sentiment_score(text)
    if score > 0.1:  return "LONG"
    if score < -0.1: return "SHORT"
    return "NEUTRAL"


def _confidence(text: str, symbol: str, direction: str,
                entry: Optional[float], sl: Optional[float],
                tps: List[float]) -> float:
    """Heuristic confidence 0 → 1."""
    score = 0.0
    if symbol:                          score += 0.20
    if direction in ("LONG","SHORT"):   score += 0.20
    if entry:                           score += 0.20
    if sl:                              score += 0.15
    if len(tps) >= 1:                   score += 0.10
    if len(tps) >= 2:                   score += 0.05
    # penalise noise
    words = text.split()
    if len(words) < 5:                  score -= 0.10
    if any(w in text.lower() for w in ["maybe","perhaps","not sure","could"]):
        score -= 0.10
    if "⚡" in text or "🚀" in text or "🎯" in text:
        score += 0.05                   # common signal emoji bonus
    return round(max(0.0, min(1.0, score)), 3)


def parse_message(msg_text: str, channel: str,
                  msg_id: int, timestamp: datetime) -> Optional[RawSignal]:
    """Parse a single Telegram message → RawSignal or None."""
    text = msg_text.strip()
    if len(text) < 10:
        return None

    symbol    = _extract_symbol(text)
    direction = _direction_from_text(text)

    # Price levels
    em = RE_ENTRY.search(text); entry  = _clean_price(em.group(1)) if em else None
    sm = RE_SL.search(text);    sl     = _clean_price(sm.group(1)) if sm else None
    tps       = _extract_take_profits(text)
    lm        = RE_LEV.search(text)
    leverage  = int(lm.group(1)) if lm else 1
    leverage  = min(max(leverage, 1), 125)

    sentiment = _sentiment_score(text)
    keywords  = (
        [m for m in BULLISH_WORDS if m in text.lower()] +
        [m for m in BEARISH_WORDS if m in text.lower()]
    )[:10]

    conf = _confidence(text, symbol, direction, entry, sl, tps)

    if direction == "NEUTRAL" and conf < E1_MIN_CONFIDENCE:
        return None

    return RawSignal(
        source_channel = channel,
        message_id     = msg_id,
        message_text   = text,
        timestamp      = timestamp,
        symbol         = symbol or "UNKNOWN",
        direction      = direction,
        entry_price    = entry,
        stop_loss      = sl,
        take_profits   = tps,
        leverage       = leverage,
        confidence     = conf,
        sentiment_score= sentiment,
        keywords       = keywords,
    )


# ══════════════════════════════════════════════════════════════════
# SENTIMENT WINDOW TRACKER
# ══════════════════════════════════════════════════════════════════

class SentimentWindow:
    """Tracks rolling sentiment across last N messages per channel."""

    def __init__(self, size: int = E1_SENTIMENT_WINDOW):
        self._size  = size
        self._store: Dict[str, List[float]] = {}

    def add(self, channel: str, score: float) -> None:
        self._store.setdefault(channel, []).append(score)
        if len(self._store[channel]) > self._size:
            self._store[channel].pop(0)

    def average(self, channel: str) -> float:
        vals = self._store.get(channel, [])
        return sum(vals) / len(vals) if vals else 0.0

    def channel_bias(self, channel: str) -> str:
        avg = self.average(channel)
        if avg > 0.15:  return "BULLISH"
        if avg < -0.15: return "BEARISH"
        return "NEUTRAL"


# ══════════════════════════════════════════════════════════════════
# ENGINE 1 — MAIN CLASS
# ══════════════════════════════════════════════════════════════════

class Engine1Telegram:
    """
    Usage:
        engine1 = Engine1Telegram(on_signal_callback)
        await engine1.start()
    The callback receives a list[RawSignal] ready for Engine 2.
    """
    
    def __init__(self, on_signals: Callable[[List[RawSignal]], Any],
                 history_limit: int = 5000, last_message_id: int = 0):
       self.on_signals     = on_signals
       self.history_limit  = history_limit       # ⬅️ NEW
       self.last_message_id = last_message_id    # ⬅️ NEW
       self.client         = TelegramClient(
           TELEGRAM_SESSION_FILE, TELEGRAM_API_ID, TELEGRAM_API_HASH
       )
       self.sentiment      = SentimentWindow()
       self._seen_ids: Dict[str, set] = {}
       self._running       = False
    
    
    # ── Connection ─────────────────────────────────────────────────

    async def start(self) -> None:
        log.info("Engine 1 starting — connecting to Telegram …")
        await self.client.start(phone=TELEGRAM_PHONE)
        log.info("Telegram session active")

        # Resolve channels
        self._channels = []
        for ch in TELEGRAM_CHANNELS:
            ch = ch.strip()
            if not ch:
                continue
            try:
                entity = await self.client.get_entity(ch)
                self._channels.append(entity)
                self._seen_ids[ch] = set()
                log.info("Channel resolved: %s", ch)
            except Exception as e:
                log.error("Cannot resolve channel '%s': %s", ch, e)

        if not self._channels:
            log.warning("No valid channels configured — check TELEGRAM_CHANNELS in .env")

        # Scan history
        await self._scan_history()

        # Register live listener
        self.client.add_event_handler(
            self._on_new_message,
            events.NewMessage(chats=self._channels),
        )
        self._running = True
        log.info("Engine 1 listening for live messages …")
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        self._running = False
        await self.client.disconnect()

    # ── Historical scan ────────────────────────────────────────────

    async def _scan_history(self) -> None:
        log.info("Scanning message history (limit=%d per channel) …",
                 TELEGRAM_HISTORY_LIMIT)
        all_signals: List[RawSignal] = []

        for entity in self._channels:
            ch_name = getattr(entity, "username", str(entity.id)) or str(entity.id)
            count = 0
            async for msg in self.client.iter_messages(
                entity, limit=TELEGRAM_HISTORY_LIMIT
            ):
                if not isinstance(msg, Message) or not msg.text:
                    continue
                sig = self._process_msg(msg, ch_name)
                if sig:
                    all_signals.append(sig)
                    count += 1
            log.info("History scan '%s': %d signals extracted", ch_name, count)

        if all_signals:
            log.info("Total historical signals: %d — forwarding to Engine 2", len(all_signals))
            await self._emit(all_signals)

    # ── Live message handler ───────────────────────────────────────

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        msg = event.message
        if not msg or not msg.text:
            return
        ch_name = str(event.chat_id)
        sig = self._process_msg(msg, ch_name)
        if sig:
            log.info("[E1] NEW LIVE SIGNAL  %s  %s  conf=%.2f",
                     sig.symbol, sig.direction, sig.confidence)
            await self._emit([sig])

    # ── Core processing ────────────────────────────────────────────

    def _process_msg(self, msg: Message, channel: str) -> Optional[RawSignal]:
        seen = self._seen_ids.setdefault(channel, set())
        if msg.id in seen:
            return None
        seen.add(msg.id)

        ts = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
        sig = parse_message(msg.text, channel, msg.id, ts)

        if sig and sig.confidence >= E1_MIN_CONFIDENCE:
            self.sentiment.add(channel, sig.sentiment_score)
            sig.raw_metadata["channel_bias"] = self.sentiment.channel_bias(channel)
            # persist
            try:
                save_raw_signal(sig)
            except Exception as e:
                log.warning("DB save failed: %s", e)
            return sig
        return None

    async def _emit(self, signals: List[RawSignal]) -> None:
        try:
            result = self.on_signals(signals)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.error("Engine 1 emit error: %s", e, exc_info=True)

    # ── Utility ────────────────────────────────────────────────────

    def get_channel_sentiment(self) -> Dict[str, str]:
        return {
            ch: self.sentiment.channel_bias(ch)
            for ch in self._seen_ids
        }
