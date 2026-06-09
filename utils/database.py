"""
Lightweight SQLite persistence layer.
Stores signals, trade records, and account snapshots.
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Optional
from config import DB_PATH
from utils.logger import get_logger

log = get_logger("Database")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    """Create all tables on first run."""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel       TEXT,
            message_id    INTEGER,
            symbol        TEXT,
            direction     TEXT,
            entry_price   REAL,
            stop_loss     REAL,
            take_profits  TEXT,
            confidence    REAL,
            sentiment     REAL,
            message_text  TEXT,
            created_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS analyzed_signals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_signal_id       INTEGER,
            symbol              TEXT,
            direction           TEXT,
            entry_price         REAL,
            stop_loss           REAL,
            take_profits        TEXT,
            overall_confidence  REAL,
            indicator_snapshot  TEXT,
            created_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol            TEXT,
            direction         TEXT,
            entry_price       REAL,
            exit_price        REAL,
            quantity          REAL,
            leverage          INTEGER,
            stop_loss         REAL,
            take_profits      TEXT,
            order_type        TEXT,
            binance_order_id  TEXT,
            status            TEXT DEFAULT 'PENDING',
            pnl_usdt          REAL DEFAULT 0,
            pnl_pct           REAL DEFAULT 0,
            confidence        REAL,
            upgrades_applied  TEXT,
            source_channel    TEXT,
            signal_text       TEXT,
            opened_at         TEXT,
            closed_at         TEXT
        );

        CREATE TABLE IF NOT EXISTS account_snapshots (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            total_wallet_balance  REAL,
            available_balance     REAL,
            total_unrealized_pnl  REAL,
            total_margin_used     REAL,
            open_positions        INTEGER,
            daily_pnl_usdt        REAL,
            daily_pnl_pct         REAL,
            max_drawdown_pct      REAL,
            captured_at           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        """)
    log.info("Database initialised at %s", DB_PATH)


# ─── Signal helpers ────────────────────────────────────────────────────────────

def save_raw_signal(sig) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO raw_signals
               (channel,message_id,symbol,direction,entry_price,stop_loss,
                take_profits,confidence,sentiment,message_text,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sig.source_channel, sig.message_id, sig.symbol, sig.direction,
             sig.entry_price, sig.stop_loss, json.dumps(sig.take_profits),
             sig.confidence, sig.sentiment_score, sig.message_text[:2000],
             sig.timestamp.isoformat()),
        )
        return cur.lastrowid


def save_trade(trade) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO trades
               (symbol,direction,entry_price,exit_price,quantity,leverage,
                stop_loss,take_profits,order_type,binance_order_id,status,
                pnl_usdt,pnl_pct,confidence,upgrades_applied,source_channel,
                signal_text,opened_at,closed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade.symbol, trade.direction, trade.entry_price, trade.exit_price,
             trade.quantity, trade.leverage, trade.stop_loss,
             trade.take_profits, trade.order_type, trade.binance_order_id,
             trade.status, trade.pnl_usdt, trade.pnl_pct, trade.confidence,
             trade.upgrades_applied, trade.source_channel, trade.signal_text[:2000],
             trade.opened_at.isoformat(),
             trade.closed_at.isoformat() if trade.closed_at else None),
        )
        return cur.lastrowid


def get_open_trades() -> List[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at DESC"
        ).fetchall()


def update_trade_status(trade_id: int, status: str,
                        exit_price: float = 0.0,
                        pnl_usdt: float = 0.0,
                        pnl_pct: float = 0.0) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE trades SET status=?,exit_price=?,pnl_usdt=?,pnl_pct=?,
               closed_at=? WHERE id=?""",
            (status, exit_price, pnl_usdt, pnl_pct,
             datetime.utcnow().isoformat(), trade_id),
        )


def get_daily_pnl() -> float:
    today = datetime.utcnow().date().isoformat()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(pnl_usdt),0) FROM trades WHERE closed_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return float(row[0])


def save_account_snapshot(snap) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO account_snapshots
               (total_wallet_balance,available_balance,total_unrealized_pnl,
                total_margin_used,open_positions,daily_pnl_usdt,daily_pnl_pct,
                max_drawdown_pct,captured_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (snap.total_wallet_balance, snap.available_balance,
             snap.total_unrealized_pnl, snap.total_margin_used,
             snap.open_positions, snap.daily_pnl_usdt, snap.daily_pnl_pct,
             snap.max_drawdown_pct, snap.captured_at.isoformat()),
        )
