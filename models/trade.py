"""
Trade execution and record models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class TradeRecord:
    """Persisted record of a live or simulated trade."""
    id              : Optional[int]  = None
    symbol          : str            = ""
    direction       : str            = ""        # LONG / SHORT
    entry_price     : float          = 0.0
    exit_price      : Optional[float]= None
    quantity        : float          = 0.0
    leverage        : int            = 1
    stop_loss       : float          = 0.0
    take_profits    : str            = "[]"      # JSON array stored as string
    order_type      : str            = "MARKET"
    binance_order_id: Optional[str]  = None
    status          : str            = "PENDING" # PENDING/OPEN/CLOSED/CANCELLED
    pnl_usdt        : float          = 0.0
    pnl_pct         : float          = 0.0
    confidence      : float          = 0.0
    upgrades_applied: str            = "[]"      # JSON array
    source_channel  : str            = ""
    signal_text     : str            = ""
    opened_at       : datetime       = field(default_factory=datetime.utcnow)
    closed_at       : Optional[datetime] = None
    metadata        : Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountSnapshot:
    """Point-in-time snapshot of the Binance account."""
    total_wallet_balance  : float
    available_balance     : float
    total_unrealized_pnl  : float
    total_margin_used     : float
    open_positions        : int
    daily_pnl_usdt        : float
    daily_pnl_pct         : float
    max_drawdown_pct      : float
    captured_at           : datetime = field(default_factory=datetime.utcnow)
