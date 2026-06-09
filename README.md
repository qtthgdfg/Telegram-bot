# 🤖 Crypto Signal Bot — 5-Engine Trading System

A production-grade Python bot that reads Telegram crypto signal channels,
performs deep multi-model analysis, and executes risk-managed trades on
Binance Futures.

---

## Architecture — 5 Engines

```
Telegram Channels
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENGINE 1 — Telegram Intelligence                               │
│  • Telethon client (history + live)                             │
│  • NLP: symbol, direction, entry, SL, TPs, leverage extraction  │
│  • Sentiment scoring (bullish/bearish word lexicon)             │
│  • Rolling channel-level sentiment window                       │
│  • Confidence scoring (0–1) per message                         │
│  • Emits: List[RawSignal]                                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENGINE 2 — Deep Market Analysis               ┐                │
│  • Fetches OHLCV on 6 timeframes (1m→1d)       │                │
│  • Runs ENGINE 5 indicators on every TF        │ ENGINE 5       │
│  • Weighted multi-timeframe consensus          │ 1000+          │
│  • Volume confirmation (OBV + CMF)             │ indicators     │
│  • Trend alignment (market structure)          │                │
│  • Auto-calculates SL/TP via ATR if missing    │                │
│  • Emits: AnalyzedSignal                       ┘                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENGINE 3 — Risk Management (ACCOUNT PROTECTION)                │
│  • Daily loss circuit-breaker  (default: −3%)                   │
│  • Max drawdown circuit-breaker (default: −8%)                  │
│  • Max concurrent open trades  (default: 5)                     │
│  • Minimum R:R ratio gate      (default: 2.0)                   │
│  • Kelly Criterion position sizing (fractional)                 │
│  • Volatility-adjusted leverage cap                             │
│  • Max portfolio exposure       (default: 20%)                  │
│  • Max single position size     (default: 5%)                   │
│  • Duplicate symbol filter                                      │
│  • Emits: RiskAssessedSignal                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENGINE 4 — Trade Optimizer & Executor                          │
│  11 Upgrade modules applied before execution:                   │
│  • LimitEntryUpgrade     — switch to limit if spread tight      │
│  • SlippageAdjust        — widen entry for fill probability     │
│  • TrailingStop          — dynamic SL for high-conf trades      │
│  • IcebergSplit          — hide large orders in 5 slices        │
│  • TPLadderUpgrade       — ensure ≥ 3 take-profit levels        │
│  • BreakevenOnTP1        — move SL to entry after TP1 hit       │
│  • ConfidenceSizeBoost   — +15% size for ≥ 85% confidence       │
│  • LowConfSizeCut        — −40% size for < 65% confidence       │
│  • IOCForScalpTF         — IOC time-in-force for 1m/5m          │
│  • FundingRateFilter     — reduce size if funding opposes        │
│  • OITrendBlock          — block long when OI collapsing         │
│  • Places: Entry + SL + TP ladder on Binance Futures            │
│  • Background monitor: breakeven move, close detection          │
└─────────────────────────────────────────────────────────────────┘
```

---

## ENGINE 5 — Indicators Library (1000+ signals)

| Category              | Indicators                                                |
|-----------------------|-----------------------------------------------------------|
| **Trend** (25)        | SMA, EMA, WMA, HMA, DEMA, TEMA, VWAP, VWMA, ALMA, KAMA, SuperTrend, Parabolic SAR, Ichimoku (5 lines), Aroon, DPO, TRIX, Mass Index, Vortex, Linear Regression, KST |
| **Momentum** (25)     | RSI, StochRSI, MACD, PPO, CCI, Williams %R, ROC, Stochastic, Ultimate Oscillator, Awesome Oscillator, TSI, CMO, DMI/ADX, Fisher Transform, Elder Ray, Squeeze Momentum, Connors RSI |
| **Volatility** (15)   | ATR, Bollinger Bands (width + %B), Keltner Channel, Donchian Channel, Chaikin Volatility, Historical Volatility, Ulcer Index, NATR, VIX Fix |
| **Volume** (15)       | OBV, CMF, MFI, VPT, A/D Line, ADOSC, Force Index, EOM, Volume RSI, Klinger Oscillator, Twiggs Money Flow, NVI, PVI, Volume Oscillator |
| **S/R** (6)           | Classic Pivots, Camarilla Pivots, Fibonacci Retracement (9 levels), Price Density Zones, Swing Highs, Swing Lows |
| **Candlestick** (10+) | Doji, Hammer, Shooting Star, Engulfing (Bull/Bear), Morning/Evening Star, Three White Soldiers, Three Black Crows, Harami |
| **Market Structure**  | Higher Highs/Lows, Lower Highs/Lows, Trend Bias, Range Detection |

Every candle on every timeframe is scored.  The final `consensus_score` is a
weighted combination of all signals — ranging from −1 (strong bear) to +1
(strong bull).

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env with your Telegram API credentials, Binance keys, and channel list
```

### 3. Run in dry-run mode first (no real orders)
```bash
DRY_RUN=true python main.py
```

### 4. Switch to live mode (after thorough testing)
```bash
# In .env:  BINANCE_TESTNET=false  DRY_RUN=false
python main.py
```

---

## Risk Settings (config.py)

| Parameter                   | Default | Description                              |
|-----------------------------|---------|------------------------------------------|
| `E3_MAX_ACCOUNT_RISK_PCT`   | 1.0%    | Max account at risk per trade            |
| `E3_MAX_PORTFOLIO_EXPOSURE` | 20.0%   | Max total margin across all positions    |
| `E3_MAX_SINGLE_POSITION_PCT`| 5.0%    | Max margin for a single trade            |
| `E3_MAX_DAILY_LOSS_PCT`     | 3.0%    | Daily loss circuit-breaker               |
| `E3_MAX_DRAWDOWN_PCT`       | 8.0%    | Drawdown circuit-breaker                 |
| `E3_DEFAULT_RR_RATIO`       | 2.0     | Minimum reward:risk ratio                |
| `E3_MAX_OPEN_TRADES`        | 5       | Max concurrent open positions            |
| `E3_KELLY_FRACTION`         | 0.25    | Fractional Kelly (conservative)          |
| `E2_CONSENSUS_THRESHOLD`    | 0.60    | Min indicator agreement to trade         |

---

## File Structure

```
crypto_signal_bot/
├── main.py                       ← Orchestrator / entry point
├── config.py                     ← All tunable parameters
├── requirements.txt
├── .env.example                  ← Copy to .env
├── engines/
│   ├── engine1_telegram.py       ← Telegram reader + NLP
│   ├── engine2_analyzer.py       ← Multi-TF market analysis
│   ├── engine3_risk.py           ← Risk management + sizing
│   ├── engine4_optimizer.py      ← Trade optimizer + executor
│   └── engine5_indicators.py     ← 1000+ indicator library
├── models/
│   ├── signal.py                 ← RawSignal, AnalyzedSignal, etc.
│   └── trade.py                  ← TradeRecord, AccountSnapshot
├── utils/
│   ├── binance_client.py         ← Binance Futures REST wrapper
│   ├── database.py               ← SQLite persistence
│   ├── logger.py                 ← Rotating file + console logger
│   └── notifier.py               ← Telegram bot + Discord alerts
├── data/                         ← SQLite DB + Telegram session
└── logs/                         ← Rotating log files
```

---

## Notifications

The bot sends alerts to your personal Telegram bot and/or a Discord channel:
- ✅ New signal detected
- 🚀 Trade opened (with full details)
- ✅/❌ Trade closed (with PnL)
- 🛑 Circuit breaker triggered

---

## ⚠️ Disclaimer

This software is for educational purposes.  Crypto trading carries
substantial risk of loss.  Always test on testnet first.  Never risk
capital you cannot afford to lose.  The authors take no responsibility
for financial losses.
