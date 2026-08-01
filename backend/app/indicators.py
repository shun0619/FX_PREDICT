"""テクニカル指標(pandasのみで実装、ta-lib不要)。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(s, fast) - ema(s, slow)
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    m = sma(s, n)
    sd = s.rolling(n).std()
    return m + k * sd, m, m - k * sd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def ichimoku(df: pd.DataFrame, conv: int = 9, base: int = 26, span_b: int = 52):
    """一目均衡表。転換線・基準線・先行スパンA/B(雲)を返す。

    先行スパンは base 本先にずらして描くのが定石だが、特徴量として使うときは
    「現在の価格が雲のどこにいるか」を見たいので、ずらした後の値を返す。
    shift(base) は過去の値を現在に持ってくる操作なので先読みにはならない。
    """
    h, l = df["high"], df["low"]
    tenkan = (h.rolling(conv).max() + l.rolling(conv).min()) / 2
    kijun = (h.rolling(base).max() + l.rolling(base).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(base)
    senkou_b = ((h.rolling(span_b).max() + l.rolling(span_b).min()) / 2).shift(base)
    return tenkan, kijun, senkou_a, senkou_b


def _pivots(s: pd.Series, left: int, right: int, high: bool) -> pd.Series:
    """スイング高値/安値。確定判定に right 本必要なので shift(right) で
    「確定した時点」に合わせる(先読み防止)。"""
    win = left + right + 1
    ext = s.rolling(win, center=False).max() if high else s.rolling(win).min()
    is_piv = (s.shift(right) == ext)
    return s.shift(right).where(is_piv).ffill()


def divergence(price: pd.Series, osc: pd.Series, lookback: int = 30) -> pd.Series:
    """ダイバージェンス。価格と指標(RSI/MACD)の食い違いを -1〜+1 で返す。

    +1: 強気(価格は安値更新したが指標は切り上げ = 下落の勢い減衰)
    -1: 弱気(価格は高値更新したが指標は切り下げ = 上昇の勢い減衰)
    """
    p_min = price.rolling(lookback).min()
    p_max = price.rolling(lookback).max()
    o_at_pmin = osc.where(price == p_min).ffill()
    o_at_pmax = osc.where(price == p_max).ffill()
    bull = ((price <= p_min * 1.0001) & (osc > o_at_pmin.shift(1))).astype(float)
    bear = ((price >= p_max * 0.9999) & (osc < o_at_pmax.shift(1))).astype(float)
    return (bull - bear).fillna(0.0)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out["sma20"] = sma(c, 20)
    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["rsi14"] = rsi(c, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["bb_upper"], out["bb_mid"], out["bb_lower"] = bollinger(c)
    out["atr14"] = atr(out, 14)
    out["tenkan"], out["kijun"], out["senkou_a"], out["senkou_b"] = ichimoku(out)
    out["div_rsi"] = divergence(c, out["rsi14"])
    out["div_macd"] = divergence(c, out["macd_hist"])
    out["swing_high"] = _pivots(out["high"], 5, 3, high=True)
    out["swing_low"] = _pivots(out["low"], 5, 3, high=False)
    return out
