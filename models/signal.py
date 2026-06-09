"""
Signal data models shared across all engines.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any


@dataclass
class RawSignal:
    """A raw signal extracted from a Telegram message."""
    source_channel : str
    message_id     : int
    message_text   : str
    timestamp      : datetime
    symbol         : str               # e.g. "BTCUSDT"
    direction      : str               # "LONG" | "SHORT" | "NEUTRAL"
    entry_price    : Optional[float]   = None
    stop_loss      : Optional[float]   = None
    take_profits   : List[float]       = field(default_factory=list)
    leverage       : int               = 1
    confidence     : float             = 0.0   # 0-1 NLP confidence
    sentiment_score: float             = 0.0   # -1 to +1
    keywords       : List[str]         = field(default_factory=list)
    raw_metadata   : Dict[str, Any]    = field(default_factory=dict)


@dataclass
class AnalyzedSignal:
    """Engine 2 output — market-confirmed signal."""
    raw_signal         : RawSignal
    symbol             : str
    direction          : str            # "LONG" | "SHORT"
    entry_price        : float
    stop_loss          : float
    take_profits       : List[float]
    timeframe_agreement: float          # 0-1 fraction of TFs agreeing
    indicator_score    : float          # 0-1 weighted indicator consensus
    volume_confirmed   : bool
    trend_aligned      : bool
    overall_confidence : float          # combined score
    market_context     : Dict[str, Any] = field(default_factory=dict)
    indicator_snapshot : Dict[str, Any] = field(default_factory=dict)
    created_at         : datetime       = field(default_factory=datetime.utcnow)


@dataclass
class RiskAssessedSignal:
    """Engine 3 output — signal with position sizing and risk limits applied."""
    analyzed_signal    : AnalyzedSignal
    symbol             : str
    direction          : str
    entry_price        : float
    stop_loss          : float
    take_profits       : List[float]
    position_size_usdt : float
    position_size_qty  : float
    risk_amount_usdt   : float
    rr_ratio           : float
    leverage           : int
    account_risk_pct   : float
    approved           : bool
    rejection_reason   : Optional[str]  = None
    circuit_breaker    : bool           = False
    risk_metadata      : Dict[str, Any] = field(default_factory=dict)
    created_at         : datetime       = field(default_factory=datetime.utcnow)


@dataclass
class OptimizedOrder:
    """Engine 4 output — final trade instruction ready for execution."""
    risk_signal        : RiskAssessedSignal
    symbol             : str
    direction          : str
    order_type         : str            # "MARKET" | "LIMIT" | "ICEBERG"
    entry_price        : float
    stop_loss          : float
    take_profits       : List[float]
    trailing_stop_pct  : Optional[float]
    quantity           : float
    leverage           : int
    time_in_force      : str            # "GTC" | "IOC" | "FOK"
    iceberg_qty        : Optional[float]
    upgrades_applied   : List[str]      = field(default_factory=list)
    final_confidence   : float          = 0.0
    execute            : bool           = False
    execution_notes    : str            = ""
    created_at         : datetime       = field(default_factory=datetime.utcnow)
