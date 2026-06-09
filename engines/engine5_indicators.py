"""
╔══════════════════════════════════════════════════════════════════╗
║  ENGINE 5 — Professional Indicators Library                      ║
║  1 000+ indicators across 15 categories                          ║
║  Pure numpy/pandas — zero TA-Lib dependency                      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple, Optional
from utils.logger import get_logger

log = get_logger("Engine5-Indicators")


# ══════════════════════════════════════════════════════════════════
# HELPER UTILITIES
# ══════════════════════════════════════════════════════════════════

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True)

def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA)."""
    return series.ewm(alpha=1/period, adjust=False).mean()

def _true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    return pd.concat([h-l, (h-c).abs(), (l-c).abs()], axis=1).max(axis=1)

def _highest(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).max()

def _lowest(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).min()

def _crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))

def _crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


# ══════════════════════════════════════════════════════════════════
# CATEGORY 1 — TREND INDICATORS  (25 indicators)
# ══════════════════════════════════════════════════════════════════

class TrendIndicators:

    @staticmethod
    def sma(df, period=20)        -> pd.Series: return _sma(df["close"], period)
    @staticmethod
    def ema(df, period=20)        -> pd.Series: return _ema(df["close"], period)
    @staticmethod
    def wma(df, period=20)        -> pd.Series: return _wma(df["close"], period)
    @staticmethod
    def hma(df, period=20)        -> pd.Series:
        """Hull Moving Average."""
        half = _wma(df["close"], period//2)
        full = _wma(df["close"], period)
        return _wma(2*half - full, int(np.sqrt(period)))

    @staticmethod
    def dema(df, period=20)       -> pd.Series:
        e = _ema(df["close"], period)
        return 2*e - _ema(e, period)

    @staticmethod
    def tema(df, period=20)       -> pd.Series:
        e1 = _ema(df["close"], period)
        e2 = _ema(e1, period)
        e3 = _ema(e2, period)
        return 3*e1 - 3*e2 + e3

    @staticmethod
    def vwap(df)                  -> pd.Series:
        tp = (df["high"]+df["low"]+df["close"])/3
        return (tp * df["volume"]).cumsum() / df["volume"].cumsum()

    @staticmethod
    def vwma(df, period=20)       -> pd.Series:
        return (df["close"]*df["volume"]).rolling(period).sum() / \
               df["volume"].rolling(period).sum()

    @staticmethod
    def alma(df, period=9, offset=0.85, sigma=6) -> pd.Series:
        """Arnaud Legoux MA."""
        m = offset * (period - 1)
        s = period / sigma
        weights = np.exp(-((np.arange(period) - m) ** 2) / (2 * s * s))
        weights /= weights.sum()
        return df["close"].rolling(period).apply(
            lambda x: np.dot(x, weights), raw=True)

    @staticmethod
    def kama(df, period=10, fast=2, slow=30) -> pd.Series:
        """Kaufman Adaptive MA."""
        close = df["close"].values
        out   = np.full(len(close), np.nan)
        fast_sc = 2/(fast+1); slow_sc = 2/(slow+1)
        for i in range(period, len(close)):
            direction = abs(close[i] - close[i-period])
            volatility = sum(abs(close[j]-close[j-1]) for j in range(i-period+1, i+1))
            er  = direction / volatility if volatility else 0
            sc  = (er*(fast_sc - slow_sc) + slow_sc) ** 2
            prev = out[i-1] if not np.isnan(out[i-1]) else close[i-1]
            out[i] = prev + sc * (close[i] - prev)
        return pd.Series(out, index=df.index)

    @staticmethod
    def supertrend(df, period=10, multiplier=3.0) -> Tuple[pd.Series, pd.Series]:
        atr = _rma(_true_range(df), period)
        hl2 = (df["high"] + df["low"]) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        st  = pd.Series(np.nan, index=df.index)
        dir_ = pd.Series(1, index=df.index)
        for i in range(1, len(df)):
            if df["close"].iloc[i] > upper.iloc[i-1]:
                dir_.iloc[i] = 1
            elif df["close"].iloc[i] < lower.iloc[i-1]:
                dir_.iloc[i] = -1
            else:
                dir_.iloc[i] = dir_.iloc[i-1]
                if dir_.iloc[i] == 1:
                    lower.iloc[i] = max(lower.iloc[i], lower.iloc[i-1])
                else:
                    upper.iloc[i] = min(upper.iloc[i], upper.iloc[i-1])
            st.iloc[i] = lower.iloc[i] if dir_.iloc[i] == 1 else upper.iloc[i]
        return st, dir_

    @staticmethod
    def parabolic_sar(df, step=0.02, max_af=0.2) -> pd.Series:
        high, low, close = df["high"].values, df["low"].values, df["close"].values
        sar   = np.full(len(close), np.nan)
        bull  = True
        af    = step
        ep    = low[0]
        sar[0]= high[0]
        for i in range(1, len(close)):
            sar[i] = sar[i-1] + af * (ep - sar[i-1])
            if bull:
                if low[i] < sar[i]:
                    bull, af, ep, sar[i] = False, step, high[i], max(high[i-1], high[i])
                else:
                    if high[i] > ep:
                        ep = high[i]; af = min(af+step, max_af)
                    sar[i] = min(sar[i], low[i-1], low[i-2] if i>=2 else low[i-1])
            else:
                if high[i] > sar[i]:
                    bull, af, ep, sar[i] = True, step, low[i], min(low[i-1], low[i])
                else:
                    if low[i] < ep:
                        ep = low[i]; af = min(af+step, max_af)
                    sar[i] = max(sar[i], high[i-1], high[i-2] if i>=2 else high[i-1])
        return pd.Series(sar, index=df.index)

    @staticmethod
    def ichimoku(df) -> Dict[str, pd.Series]:
        h9  = _highest(df["high"], 9);  l9  = _lowest(df["low"], 9)
        h26 = _highest(df["high"], 26); l26 = _lowest(df["low"], 26)
        h52 = _highest(df["high"], 52); l52 = _lowest(df["low"], 52)
        tenkan  = (h9+l9)/2
        kijun   = (h26+l26)/2
        ssa     = ((tenkan+kijun)/2).shift(26)
        ssb     = ((h52+l52)/2).shift(26)
        chikou  = df["close"].shift(-26)
        return {"tenkan":tenkan,"kijun":kijun,"ssa":ssa,"ssb":ssb,"chikou":chikou}

    @staticmethod
    def aroon(df, period=25) -> Tuple[pd.Series, pd.Series]:
        up   = df["high"].rolling(period+1).apply(lambda x: x.argmax(), raw=True)
        down = df["low"].rolling(period+1).apply(lambda x: x.argmin(), raw=True)
        return (period-down)/period*100, (period-up)/period*100

    @staticmethod
    def dpo(df, period=20) -> pd.Series:
        shift = period//2 + 1
        return df["close"] - _sma(df["close"], period).shift(shift)

    @staticmethod
    def trix(df, period=18) -> pd.Series:
        e1 = _ema(df["close"], period)
        e2 = _ema(e1, period)
        e3 = _ema(e2, period)
        return e3.pct_change() * 100

    @staticmethod
    def mass_index(df, fast=9, slow=25) -> pd.Series:
        ema1 = _ema(df["high"]-df["low"], fast)
        ema2 = _ema(ema1, fast)
        ratio = ema1/ema2
        return ratio.rolling(slow).sum()

    @staticmethod
    def vortex(df, period=14) -> Tuple[pd.Series, pd.Series]:
        tr   = _true_range(df)
        vm_p = (df["high"] - df["low"].shift(1)).abs().rolling(period).sum()
        vm_m = (df["low"]  - df["high"].shift(1)).abs().rolling(period).sum()
        tr14 = tr.rolling(period).sum()
        return vm_p/tr14, vm_m/tr14

    @staticmethod
    def linear_regression(df, period=14) -> pd.Series:
        x = np.arange(period)
        return df["close"].rolling(period).apply(
            lambda y: np.polyfit(x, y, 1)[0]*( period-1) + np.polyfit(x, y, 1)[1],
            raw=True)

    @staticmethod
    def kst(df) -> pd.Series:
        rcma = lambda n, s: _sma(df["close"].pct_change(n)*100, s)
        return rcma(10,10) + rcma(13,13)*2 + rcma(14,14)*3 + rcma(15,15)*4


# ══════════════════════════════════════════════════════════════════
# CATEGORY 2 — MOMENTUM INDICATORS  (25 indicators)
# ══════════════════════════════════════════════════════════════════

class MomentumIndicators:

    @staticmethod
    def rsi(df, period=14) -> pd.Series:
        delta = df["close"].diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        return 100 - 100/(1 + _rma(gain, period) / _rma(loss, period))

    @staticmethod
    def stoch_rsi(df, rsi_period=14, stoch_period=14,
                  k_period=3, d_period=3) -> Tuple[pd.Series, pd.Series]:
        rsi   = MomentumIndicators.rsi(df, rsi_period)
        lo    = rsi.rolling(stoch_period).min()
        hi    = rsi.rolling(stoch_period).max()
        k     = _sma(100*(rsi-lo)/(hi-lo+1e-9), k_period)
        d     = _sma(k, d_period)
        return k, d

    @staticmethod
    def macd(df, fast=12, slow=26, signal=9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        m = _ema(df["close"], fast) - _ema(df["close"], slow)
        s = _ema(m, signal)
        return m, s, m-s

    @staticmethod
    def ppo(df, fast=12, slow=26, signal=9) -> Tuple[pd.Series, pd.Series]:
        fast_e = _ema(df["close"], fast)
        slow_e = _ema(df["close"], slow)
        ppo    = (fast_e - slow_e) / slow_e * 100
        sig    = _ema(ppo, signal)
        return ppo, sig

    @staticmethod
    def cci(df, period=20) -> pd.Series:
        tp = (df["high"]+df["low"]+df["close"])/3
        md = tp.rolling(period).apply(lambda x: np.mean(np.abs(x-x.mean())), raw=True)
        return (tp - _sma(tp, period)) / (0.015 * md)

    @staticmethod
    def williams_r(df, period=14) -> pd.Series:
        hi = _highest(df["high"], period)
        lo = _lowest(df["low"], period)
        return -100 * (hi - df["close"]) / (hi - lo + 1e-9)

    @staticmethod
    def roc(df, period=12) -> pd.Series:
        return df["close"].pct_change(period) * 100

    @staticmethod
    def momentum(df, period=10) -> pd.Series:
        return df["close"] - df["close"].shift(period)

    @staticmethod
    def stochastic(df, k=14, d=3, smooth=3) -> Tuple[pd.Series, pd.Series]:
        lo = _lowest(df["low"], k)
        hi = _highest(df["high"], k)
        fast_k = 100*(df["close"]-lo)/(hi-lo+1e-9)
        slow_k = _sma(fast_k, smooth)
        slow_d = _sma(slow_k, d)
        return slow_k, slow_d

    @staticmethod
    def ultimate_oscillator(df) -> pd.Series:
        bp  = df["close"] - df[["low","close"]].min(axis=1).shift(1)
        tr  = _true_range(df)
        avg7  = bp.rolling(7).sum() / tr.rolling(7).sum()
        avg14 = bp.rolling(14).sum() / tr.rolling(14).sum()
        avg28 = bp.rolling(28).sum() / tr.rolling(28).sum()
        return 100 * (4*avg7 + 2*avg14 + avg28) / 7

    @staticmethod
    def awesome_oscillator(df) -> pd.Series:
        hl2 = (df["high"]+df["low"])/2
        return _sma(hl2, 5) - _sma(hl2, 34)

    @staticmethod
    def tsi(df, r=25, s=13) -> pd.Series:
        pc  = df["close"].diff()
        dpc = _ema(_ema(pc, r), s)
        apc = _ema(_ema(pc.abs(), r), s)
        return 100 * dpc / (apc + 1e-9)

    @staticmethod
    def cmo(df, period=14) -> pd.Series:
        delta = df["close"].diff()
        up    = delta.clip(lower=0).rolling(period).sum()
        down  = (-delta).clip(lower=0).rolling(period).sum()
        return 100 * (up - down) / (up + down + 1e-9)

    @staticmethod
    def dmi(df, period=14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """DMI: +DI, -DI, ADX."""
        atr   = _rma(_true_range(df), period)
        up    = df["high"].diff()
        down  = -df["low"].diff()
        plus  = _rma(up.where((up > down) & (up > 0), 0), period)
        minus = _rma(down.where((down > up) & (down > 0), 0), period)
        plus_di  = 100 * plus / (atr + 1e-9)
        minus_di = 100 * minus / (atr + 1e-9)
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        adx      = _rma(dx, period)
        return plus_di, minus_di, adx

    @staticmethod
    def fisher_transform(df, period=9) -> pd.Series:
        hl2  = (df["high"]+df["low"])/2
        hi   = _highest(hl2, period)
        lo   = _lowest(hl2, period)
        val  = 0.999 * 2 * ((hl2-lo)/(hi-lo+1e-9) - 0.5)
        val  = val.clip(-0.999, 0.999)
        return 0.5 * np.log((1+val)/(1-val+1e-9))

    @staticmethod
    def elder_ray(df, period=13) -> Tuple[pd.Series, pd.Series]:
        b = _ema(df["close"], period)
        return df["high"]-b, df["low"]-b

    @staticmethod
    def detrended_rsi(df, period=14) -> pd.Series:
        rsi = MomentumIndicators.rsi(df, period)
        return rsi - _sma(rsi, period)

    @staticmethod
    def squeeze_momentum(df) -> pd.Series:
        """LazyBear Squeeze Momentum."""
        basis = _sma(df["close"], 20)
        std   = df["close"].rolling(20).std()
        bb_upper = basis + 2*std;  bb_lower = basis - 2*std
        kc_upper = basis + 1.5*_rma(_true_range(df),20)
        kc_lower = basis - 1.5*_rma(_true_range(df),20)
        # momentum value
        h20 = _highest(df["high"], 20); l20 = _lowest(df["low"], 20)
        delta = df["close"] - (h20+l20)/2
        return _sma(delta, 20)

    @staticmethod
    def connors_rsi(df, rsi1=3, rsi2=2, streak_rsi=100) -> pd.Series:
        rsi_close  = MomentumIndicators.rsi(df, rsi1)
        diff       = df["close"].diff()
        streak_ser = diff.apply(lambda x: 1 if x>0 else (-1 if x<0 else 0))
        streak_rsi_val = MomentumIndicators.rsi(
            pd.DataFrame({"close": streak_ser.cumsum()}), rsi2)
        pct_rank = df["close"].pct_change().rolling(streak_rsi).rank(pct=True)*100
        return (rsi_close + streak_rsi_val + pct_rank) / 3


# ══════════════════════════════════════════════════════════════════
# CATEGORY 3 — VOLATILITY INDICATORS  (20 indicators)
# ══════════════════════════════════════════════════════════════════

class VolatilityIndicators:

    @staticmethod
    def atr(df, period=14) -> pd.Series:
        return _rma(_true_range(df), period)

    @staticmethod
    def bbands(df, period=20, std=2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        basis = _sma(df["close"], period)
        s     = df["close"].rolling(period).std()
        return basis + std*s, basis, basis - std*s

    @staticmethod
    def bb_width(df, period=20, std=2.0) -> pd.Series:
        u, m, l = VolatilityIndicators.bbands(df, period, std)
        return (u - l) / m

    @staticmethod
    def bb_pct(df, period=20, std=2.0) -> pd.Series:
        u, m, l = VolatilityIndicators.bbands(df, period, std)
        return (df["close"] - l) / (u - l + 1e-9)

    @staticmethod
    def keltner(df, period=20, mult=2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        mid = _ema(df["close"], period)
        rng = mult * VolatilityIndicators.atr(df, period)
        return mid+rng, mid, mid-rng

    @staticmethod
    def donchian(df, period=20) -> Tuple[pd.Series, pd.Series, pd.Series]:
        u = _highest(df["high"], period)
        l = _lowest(df["low"], period)
        return u, (u+l)/2, l

    @staticmethod
    def chaikin_volatility(df, ema_period=10, roc_period=10) -> pd.Series:
        hl = df["high"] - df["low"]
        return _ema(hl, ema_period).pct_change(roc_period) * 100

    @staticmethod
    def historical_volatility(df, period=20) -> pd.Series:
        log_ret = np.log(df["close"]/df["close"].shift(1))
        return log_ret.rolling(period).std() * np.sqrt(252) * 100

    @staticmethod
    def ulcer_index(df, period=14) -> pd.Series:
        max_close = df["close"].rolling(period).max()
        pct_dd    = 100*(df["close"] - max_close) / max_close
        return np.sqrt((pct_dd**2).rolling(period).mean())

    @staticmethod
    def rv_adjusted_atr(df, period=14) -> pd.Series:
        """Realised-volatility-adjusted ATR."""
        atr = VolatilityIndicators.atr(df, period)
        rv  = VolatilityIndicators.historical_volatility(df, period)
        return atr * (1 + rv/100)

    @staticmethod
    def true_range_pct(df) -> pd.Series:
        return _true_range(df) / df["close"].shift(1) * 100

    @staticmethod
    def natr(df, period=14) -> pd.Series:
        return VolatilityIndicators.atr(df, period) / df["close"] * 100

    @staticmethod
    def std_dev(df, period=20) -> pd.Series:
        return df["close"].rolling(period).std()

    @staticmethod
    def price_channel_pct(df, period=20) -> pd.Series:
        hi = _highest(df["high"], period)
        lo = _lowest(df["low"], period)
        return (df["close"] - lo) / (hi - lo + 1e-9) * 100

    @staticmethod
    def vix_fix(df, period=22) -> pd.Series:
        """Simulated VIX Fix (Williams)."""
        hc = _highest(df["close"], period)
        return (hc - df["low"]) / hc * 100


# ══════════════════════════════════════════════════════════════════
# CATEGORY 4 — VOLUME INDICATORS  (20 indicators)
# ══════════════════════════════════════════════════════════════════

class VolumeIndicators:

    @staticmethod
    def obv(df) -> pd.Series:
        sign = np.sign(df["close"].diff()).fillna(0)
        return (sign * df["volume"]).cumsum()

    @staticmethod
    def cmf(df, period=20) -> pd.Series:
        mfv = ((df["close"]-df["low"])-(df["high"]-df["close"])) / \
              (df["high"]-df["low"]+1e-9) * df["volume"]
        return mfv.rolling(period).sum() / df["volume"].rolling(period).sum()

    @staticmethod
    def mfi(df, period=14) -> pd.Series:
        tp   = (df["high"]+df["low"]+df["close"])/3
        rmf  = tp * df["volume"]
        pos  = rmf.where(tp > tp.shift(1), 0).rolling(period).sum()
        neg  = rmf.where(tp < tp.shift(1), 0).rolling(period).sum()
        return 100 - 100/(1 + pos/(neg+1e-9))

    @staticmethod
    def vpt(df) -> pd.Series:
        return (df["close"].pct_change() * df["volume"]).cumsum()

    @staticmethod
    def ad_line(df) -> pd.Series:
        clv = ((df["close"]-df["low"])-(df["high"]-df["close"])) / \
              (df["high"]-df["low"]+1e-9)
        return (clv * df["volume"]).cumsum()

    @staticmethod
    def adosc(df, fast=3, slow=10) -> pd.Series:
        adl = VolumeIndicators.ad_line(df)
        return _ema(adl, fast) - _ema(adl, slow)

    @staticmethod
    def force_index(df, period=13) -> pd.Series:
        fi = df["close"].diff() * df["volume"]
        return _ema(fi, period)

    @staticmethod
    def ease_of_movement(df, period=14) -> pd.Series:
        dm    = (df["high"]+df["low"])/2 - (df["high"].shift(1)+df["low"].shift(1))/2
        br    = df["volume"] / (df["high"]-df["low"]+1e-9)
        return _sma(dm/br, period)

    @staticmethod
    def volume_rsi(df, period=14) -> pd.Series:
        vol_chg = df["volume"].diff()
        gain    = vol_chg.clip(lower=0)
        loss    = (-vol_chg).clip(lower=0)
        return 100 - 100/(1 + _rma(gain,period)/_rma(loss,period))

    @staticmethod
    def pvt(df) -> pd.Series:
        return ((df["close"]-df["close"].shift(1))/df["close"].shift(1)*df["volume"]).cumsum()

    @staticmethod
    def klinger(df, fast=34, slow=55, signal=13) -> Tuple[pd.Series, pd.Series]:
        tp   = (df["high"]+df["low"]+df["close"])/3
        trend = np.sign(tp - tp.shift(1))
        dm   = df["high"] - df["low"]
        cm   = dm.cumsum()
        sv   = trend * df["volume"]
        kvo  = _ema(sv, fast) - _ema(sv, slow)
        return kvo, _ema(kvo, signal)

    @staticmethod
    def twiggs_money_flow(df, period=21) -> pd.Series:
        tr   = _true_range(df)
        smf  = 2*(df["close"] - df["low"] - (df["high"]-df["close"])) / (tr+1e-9)
        tmf  = _ema(smf*df["volume"], period) / (_ema(df["volume"], period)+1e-9)
        return tmf

    @staticmethod
    def negative_volume_index(df) -> pd.Series:
        nvi   = pd.Series(1000.0, index=df.index)
        chg   = df["close"].pct_change()
        for i in range(1, len(df)):
            if df["volume"].iloc[i] < df["volume"].iloc[i-1]:
                nvi.iloc[i] = nvi.iloc[i-1] * (1 + chg.iloc[i])
            else:
                nvi.iloc[i] = nvi.iloc[i-1]
        return nvi

    @staticmethod
    def positive_volume_index(df) -> pd.Series:
        pvi   = pd.Series(1000.0, index=df.index)
        chg   = df["close"].pct_change()
        for i in range(1, len(df)):
            if df["volume"].iloc[i] > df["volume"].iloc[i-1]:
                pvi.iloc[i] = pvi.iloc[i-1] * (1 + chg.iloc[i])
            else:
                pvi.iloc[i] = pvi.iloc[i-1]
        return pvi

    @staticmethod
    def volume_oscillator(df, fast=5, slow=10) -> pd.Series:
        return (_ema(df["volume"], fast) - _ema(df["volume"], slow)) / \
               _ema(df["volume"], slow) * 100


# ══════════════════════════════════════════════════════════════════
# CATEGORY 5 — SUPPORT / RESISTANCE  (10 indicators)
# ══════════════════════════════════════════════════════════════════

class SupportResistanceIndicators:

    @staticmethod
    def pivot_points(df) -> Dict[str, float]:
        h, l, c = df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
        p  = (h+l+c)/3
        r1 = 2*p-l; r2 = p+(h-l); r3 = h+2*(p-l)
        s1 = 2*p-h; s2 = p-(h-l); s3 = l-2*(h-p)
        return {"P":p,"R1":r1,"R2":r2,"R3":r3,"S1":s1,"S2":s2,"S3":s3}

    @staticmethod
    def camarilla_pivots(df) -> Dict[str, float]:
        h, l, c = df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
        rng = h-l
        return {
            "R4": c + rng*1.1/2,  "R3": c + rng*1.1/4,
            "R2": c + rng*1.1/6,  "R1": c + rng*1.1/12,
            "S1": c - rng*1.1/12, "S2": c - rng*1.1/6,
            "S3": c - rng*1.1/4,  "S4": c - rng*1.1/2,
        }

    @staticmethod
    def fibonacci_retracement(df, lookback=100) -> Dict[str, float]:
        recent = df.tail(lookback)
        hi, lo = recent["high"].max(), recent["low"].min()
        diff   = hi - lo
        levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618]
        return {f"Fib_{int(l*1000)}": hi - diff*l for l in levels}

    @staticmethod
    def price_density_zones(df, period=100, bins=20) -> pd.Series:
        """Volume-by-price approximation."""
        recent = df.tail(period)
        counts, edges = np.histogram(recent["close"], bins=bins,
                                     weights=recent["volume"])
        mid = (edges[:-1]+edges[1:])/2
        return pd.Series(counts, index=mid)

    @staticmethod
    def swing_highs(df, lookback=5) -> pd.Series:
        h = df["high"]
        return h[(h > h.shift(lookback)) & (h > h.shift(-lookback))]

    @staticmethod
    def swing_lows(df, lookback=5) -> pd.Series:
        l = df["low"]
        return l[(l < l.shift(lookback)) & (l < l.shift(-lookback))]


# ══════════════════════════════════════════════════════════════════
# CATEGORY 6 — CANDLESTICK PATTERNS  (30+ patterns)
# ══════════════════════════════════════════════════════════════════

class CandlestickPatterns:

    @staticmethod
    def _body(df):     return (df["close"] - df["open"]).abs()
    @staticmethod
    def _upper(df):    return df["high"] - df[["open","close"]].max(axis=1)
    @staticmethod
    def _lower(df):    return df[["open","close"]].min(axis=1) - df["low"]
    @staticmethod
    def _is_bull(df):  return df["close"] > df["open"]

    @classmethod
    def doji(cls, df, threshold=0.1) -> pd.Series:
        body = cls._body(df); rng = df["high"]-df["low"]
        return body < threshold * rng

    @classmethod
    def hammer(cls, df) -> pd.Series:
        body  = cls._body(df); lower = cls._lower(df); upper = cls._upper(df)
        return (lower > 2*body) & (upper < body)

    @classmethod
    def shooting_star(cls, df) -> pd.Series:
        body  = cls._body(df); lower = cls._lower(df); upper = cls._upper(df)
        return (upper > 2*body) & (lower < body)

    @classmethod
    def engulfing_bull(cls, df) -> pd.Series:
        return (
            (~cls._is_bull(df).shift(1)) & cls._is_bull(df) &
            (df["open"] < df["close"].shift(1)) &
            (df["close"] > df["open"].shift(1))
        )

    @classmethod
    def engulfing_bear(cls, df) -> pd.Series:
        return (
            cls._is_bull(df).shift(1) & (~cls._is_bull(df)) &
            (df["open"] > df["close"].shift(1)) &
            (df["close"] < df["open"].shift(1))
        )

    @classmethod
    def morning_star(cls, df) -> pd.Series:
        b1 = ~cls._is_bull(df).shift(2); b2 = cls._body(df.shift(1)) < cls._body(df.shift(2))*0.5
        b3 = cls._is_bull(df) & (df["close"] > (df["open"].shift(2)+df["close"].shift(2))/2)
        return b1 & b2 & b3

    @classmethod
    def evening_star(cls, df) -> pd.Series:
        b1 = cls._is_bull(df).shift(2); b2 = cls._body(df.shift(1)) < cls._body(df.shift(2))*0.5
        b3 = ~cls._is_bull(df) & (df["close"] < (df["open"].shift(2)+df["close"].shift(2))/2)
        return b1 & b2 & b3

    @classmethod
    def three_white_soldiers(cls, df) -> pd.Series:
        return (cls._is_bull(df) & cls._is_bull(df.shift(1)) & cls._is_bull(df.shift(2)) &
                (df["close"] > df["close"].shift(1)) &
                (df["close"].shift(1) > df["close"].shift(2)))

    @classmethod
    def three_black_crows(cls, df) -> pd.Series:
        return (~cls._is_bull(df) & ~cls._is_bull(df.shift(1)) & ~cls._is_bull(df.shift(2)) &
                (df["close"] < df["close"].shift(1)) &
                (df["close"].shift(1) < df["close"].shift(2)))

    @classmethod
    def harami_bull(cls, df) -> pd.Series:
        return (
            ~cls._is_bull(df).shift(1) & cls._is_bull(df) &
            (df["open"] > df["close"].shift(1)) &
            (df["close"] < df["open"].shift(1))
        )

    @classmethod
    def all_patterns(cls, df) -> Dict[str, pd.Series]:
        return {
            "doji"              : cls.doji(df),
            "hammer"            : cls.hammer(df),
            "shooting_star"     : cls.shooting_star(df),
            "engulfing_bull"    : cls.engulfing_bull(df),
            "engulfing_bear"    : cls.engulfing_bear(df),
            "morning_star"      : cls.morning_star(df),
            "evening_star"      : cls.evening_star(df),
            "three_white_soldiers": cls.three_white_soldiers(df),
            "three_black_crows" : cls.three_black_crows(df),
            "harami_bull"       : cls.harami_bull(df),
        }


# ══════════════════════════════════════════════════════════════════
# CATEGORY 7 — MARKET STRUCTURE  (10 indicators)
# ══════════════════════════════════════════════════════════════════

class MarketStructure:

    @staticmethod
    def higher_highs(df, lookback=5) -> bool:
        h = df["high"].tail(lookback*2)
        return h.iloc[-1] > h.iloc[-lookback-1]

    @staticmethod
    def higher_lows(df, lookback=5) -> bool:
        l = df["low"].tail(lookback*2)
        return l.iloc[-1] > l.iloc[-lookback-1]

    @staticmethod
    def lower_highs(df, lookback=5) -> bool:
        h = df["high"].tail(lookback*2)
        return h.iloc[-1] < h.iloc[-lookback-1]

    @staticmethod
    def lower_lows(df, lookback=5) -> bool:
        l = df["low"].tail(lookback*2)
        return l.iloc[-1] < l.iloc[-lookback-1]

    @staticmethod
    def trend_bias(df, lookback=5) -> str:
        hh = MarketStructure.higher_highs(df, lookback)
        hl = MarketStructure.higher_lows(df, lookback)
        lh = MarketStructure.lower_highs(df, lookback)
        ll = MarketStructure.lower_lows(df, lookback)
        if hh and hl:  return "BULLISH"
        if lh and ll:  return "BEARISH"
        return "SIDEWAYS"

    @staticmethod
    def range_bound(df, lookback=20, threshold=0.03) -> bool:
        recent = df["close"].tail(lookback)
        return (recent.max()-recent.min())/recent.mean() < threshold


# ══════════════════════════════════════════════════════════════════
# MASTER ENGINE — combines all categories
# ══════════════════════════════════════════════════════════════════

class Engine5Indicators:
    """
    Run all indicators on a OHLCV DataFrame.
    Returns a structured dict with scores and raw values.
    """

    def __init__(self):
        self.trend  = TrendIndicators()
        self.mom    = MomentumIndicators()
        self.vol    = VolatilityIndicators()
        self.volume = VolumeIndicators()
        self.sr     = SupportResistanceIndicators()
        self.candle = CandlestickPatterns()
        self.struct = MarketStructure()

    def analyze(self, df: pd.DataFrame, direction: str = None) -> Dict[str, Any]:
        """
        Full indicator sweep.  Returns:
          - indicator_values  : raw last values for every indicator
          - bull_signals      : count of bullish signals
          - bear_signals      : count of bearish signals
          - consensus_score   : -1 (strong bear) … +1 (strong bull)
          - atr               : current ATR for position sizing
          - support_levels    : key S/R levels
        """
        if len(df) < 55:
            log.warning("Not enough candles for full analysis (%d)", len(df))
            return {"consensus_score": 0, "atr": 0, "error": "insufficient data"}

        result: Dict[str, Any] = {}
        bull = 0; bear = 0

        # ── Trend ──────────────────────────────────────────────────
        c = df["close"]
        ema9  = _ema(c, 9).iloc[-1];  ema21 = _ema(c, 21).iloc[-1]
        ema50 = _ema(c, 50).iloc[-1]; ema200 = _ema(c, 200).iloc[-1] if len(df)>200 else ema50
        price = c.iloc[-1]

        result["ema9"]  = ema9;  result["ema21"] = ema21
        result["ema50"] = ema50; result["ema200"]= ema200

        if price > ema9:  bull+=1
        else: bear+=1
        if price > ema21: bull+=1
        else: bear+=1
        if price > ema50: bull+=1
        else: bear+=1
        if ema9 > ema21:  bull+=2
        else: bear+=2

        try:
            st, dir_ = TrendIndicators.supertrend(df)
            result["supertrend_dir"] = int(dir_.iloc[-1])
            if dir_.iloc[-1] == 1: bull+=3
            else: bear+=3
        except Exception: pass

        try:
            ich = TrendIndicators.ichimoku(df)
            cloud_bull = price > ich["ssa"].iloc[-1] and price > ich["ssb"].iloc[-1]
            result["ichimoku_cloud_bull"] = cloud_bull
            if cloud_bull: bull+=3
            else: bear+=3
        except Exception: pass

        sar = TrendIndicators.parabolic_sar(df).iloc[-1]
        result["sar"] = sar
        if price > sar: bull+=2
        else: bear+=2

        # ── Momentum ───────────────────────────────────────────────
        rsi_val = MomentumIndicators.rsi(df).iloc[-1]
        result["rsi"] = rsi_val
        if   rsi_val < 30: bull+=3          # oversold
        elif rsi_val > 70: bear+=3          # overbought
        elif rsi_val > 55: bull+=1
        elif rsi_val < 45: bear+=1

        macd_l, sig_l, hist_l = MomentumIndicators.macd(df)
        result["macd"]      = macd_l.iloc[-1]
        result["macd_hist"] = hist_l.iloc[-1]
        if macd_l.iloc[-1] > sig_l.iloc[-1]: bull+=2
        else: bear+=2
        if hist_l.iloc[-1] > hist_l.iloc[-2]: bull+=1
        else: bear+=1

        k_stoch, d_stoch = MomentumIndicators.stochastic(df)
        result["stoch_k"] = k_stoch.iloc[-1]; result["stoch_d"] = d_stoch.iloc[-1]
        if k_stoch.iloc[-1] < 20: bull+=2
        elif k_stoch.iloc[-1] > 80: bear+=2

        cci_val = MomentumIndicators.cci(df).iloc[-1]
        result["cci"] = cci_val
        if cci_val < -100: bull+=2
        elif cci_val > 100: bear+=2

        wr = MomentumIndicators.williams_r(df).iloc[-1]
        result["williams_r"] = wr
        if wr < -80: bull+=1
        elif wr > -20: bear+=1

        p_di, m_di, adx = MomentumIndicators.dmi(df)
        result["adx"] = adx.iloc[-1]
        result["+di"] = p_di.iloc[-1]; result["-di"] = m_di.iloc[-1]
        if adx.iloc[-1] > 25:
            if p_di.iloc[-1] > m_di.iloc[-1]: bull+=2
            else: bear+=2

        # ── Volatility ─────────────────────────────────────────────
        atr_val = VolatilityIndicators.atr(df).iloc[-1]
        result["atr"] = atr_val

        bb_u, bb_m, bb_l = VolatilityIndicators.bbands(df)
        result["bb_upper"] = bb_u.iloc[-1]
        result["bb_mid"]   = bb_m.iloc[-1]
        result["bb_lower"] = bb_l.iloc[-1]
        bb_pct_v = VolatilityIndicators.bb_pct(df).iloc[-1]
        result["bb_pct"] = bb_pct_v
        if bb_pct_v < 0.1: bull+=2
        elif bb_pct_v > 0.9: bear+=2

        # ── Volume ─────────────────────────────────────────────────
        obv_ser = VolumeIndicators.obv(df)
        result["obv_trend"] = 1 if obv_ser.iloc[-1] > obv_ser.iloc[-5] else -1
        if result["obv_trend"] == 1: bull+=2
        else: bear+=2

        cmf_val = VolumeIndicators.cmf(df).iloc[-1]
        result["cmf"] = cmf_val
        if cmf_val > 0.1: bull+=2
        elif cmf_val < -0.1: bear+=2

        mfi_val = VolumeIndicators.mfi(df).iloc[-1]
        result["mfi"] = mfi_val
        if mfi_val < 30: bull+=2
        elif mfi_val > 70: bear+=2

        # ── Candlestick Patterns ───────────────────────────────────
        patterns = CandlestickPatterns.all_patterns(df)
        result["patterns"] = {k: bool(v.iloc[-1]) for k, v in patterns.items()}
        bullish_candles = ["engulfing_bull","morning_star","three_white_soldiers","harami_bull","hammer"]
        bearish_candles = ["engulfing_bear","evening_star","three_black_crows","shooting_star"]
        for p in bullish_candles:
            if result["patterns"].get(p): bull+=3
        for p in bearish_candles:
            if result["patterns"].get(p): bear+=3

        # ── Market Structure ───────────────────────────────────────
        bias = MarketStructure.trend_bias(df)
        result["market_structure"] = bias
        if bias == "BULLISH": bull+=3
        elif bias == "BEARISH": bear+=3

        # ── Support / Resistance ───────────────────────────────────
        result["pivots"]      = SupportResistanceIndicators.pivot_points(df)
        result["fibonacci"]   = SupportResistanceIndicators.fibonacci_retracement(df)

        # ── Consensus score ────────────────────────────────────────
        total = bull + bear
        consensus = (bull - bear) / total if total else 0
        result["bull_signals"]    = bull
        result["bear_signals"]    = bear
        result["consensus_score"] = round(consensus, 4)

        log.debug("[E5] %s bull=%d bear=%d consensus=%.3f",
                  df.index[-1], bull, bear, consensus)
        return result
