"""
=============================================================
  CRYPTO SIGNAL BOT — Global Configuration
=============================================================
  All tunable parameters live here.
  Secrets are loaded from .env (never hard-coded).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ──────────────────────────────────────────────────────────
TELEGRAM_API_ID        = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH      = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE         = os.getenv("TELEGRAM_PHONE", "")
TELEGRAM_SESSION_FILE  = "data/telegram_session"
TELEGRAM_CHANNELS      = os.getenv("TELEGRAM_CHANNELS", "").split(",")   # comma-separated usernames/IDs
TELEGRAM_HISTORY_LIMIT = int(os.getenv("TELEGRAM_HISTORY_LIMIT") or "5000")  # messages to scan on first run

# ─── Binance ──────────────────────────────────────────────────────────
BINANCE_API_KEY        = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET     = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET        = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
BINANCE_BASE_URL       = (
    "https://testnet.binance.vision" if BINANCE_TESTNET
    else "https://api.binance.com"
)

# ─── Engine 1 — Telegram Intelligence ───────────────────────────────────────
E1_SCAN_INTERVAL_SEC   = 30          # how often to poll for new messages
E1_MIN_CONFIDENCE      = 0.45        # minimum NLP confidence to keep a signal
E1_SENTIMENT_WINDOW    = 50          # last N messages for sentiment window

# ─── Engine 2 — Deep Market Analysis ────────────────────────────────────────
E2_CANDLE_INTERVALS    = ["1m","5m","15m","1h","4h","1d"]
E2_CANDLE_LIMIT        = 500         # candles per interval
E2_CONSENSUS_THRESHOLD = 0.60        # min agreement fraction to emit a signal
E2_CONFIRMATION_RULES  = True        # require multi-timeframe confirmation

# ─── Engine 3 — Risk Management ──────────────────────────────────────────────
E3_MAX_ACCOUNT_RISK_PCT     = 1.0    # max % of account at risk per trade
E3_MAX_PORTFOLIO_EXPOSURE   = 20.0   # max % of account in open positions
E3_MAX_SINGLE_POSITION_PCT  = 5.0    # max % per single position
E3_MAX_DAILY_LOSS_PCT       = 3.0    # circuit-breaker: pause if daily PnL < -3%
E3_MAX_DRAWDOWN_PCT         = 8.0    # circuit-breaker: pause if drawdown > 8%
E3_DEFAULT_RR_RATIO         = 2.0    # minimum reward:risk ratio
E3_ATR_MULTIPLIER_SL        = 1.5    # ATR multiplier for stop-loss
E3_ATR_MULTIPLIER_TP        = 3.0    # ATR multiplier for take-profit
E3_MAX_OPEN_TRADES          = 5      # concurrent open trades
E3_KELLY_FRACTION           = 0.25   # fractional Kelly (conservative)

# ─── Engine 4 — Trade Optimizer ──────────────────────────────────────────────
E4_SLIPPAGE_EST_PCT         = 0.05   # estimated slippage %
E4_ORDER_BOOK_DEPTH         = 20     # levels to analyse for liquidity
E4_ICEBERG_THRESHOLD_USDT   = 500    # use iceberg orders above this size
E4_MIN_UPGRADE_CONFIDENCE   = 0.55   # ML confidence threshold for upgrade
E4_ENABLE_TRAILING_STOP     = True
E4_TRAILING_STOP_PCT        = 1.0    # trail distance %

# ─── Engine 5 — Indicators ───────────────────────────────────────────────────
E5_FAST_MA      = 9
E5_SLOW_MA      = 21
E5_SIGNAL_MA    = 9
E5_RSI_PERIOD   = 14
E5_ATR_PERIOD   = 14
E5_BBANDS_STD   = 2.0
E5_VOLUME_MA    = 20

# ─── Database ──────────────────────────────────────────────────────────
DB_PATH = "data/signals.db"

# ─── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = "logs/bot.log"

# ─── Notifications ────────────────────────────────────────────────────
NOTIFY_TELEGRAM_BOT_TOKEN  = os.getenv("NOTIFY_TELEGRAM_BOT_TOKEN", "")
NOTIFY_TELEGRAM_CHAT_ID    = os.getenv("NOTIFY_TELEGRAM_CHAT_ID", "")
NOTIFY_DISCORD_WEBHOOK      = os.getenv("NOTIFY_DISCORD_WEBHOOK", "")
