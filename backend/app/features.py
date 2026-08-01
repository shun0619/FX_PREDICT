"""特徴量生成とラベル定義。

- 自時間足のテクニカル特徴量 (18)
- 上位足トレンド特徴量 (6): 確定足のみ使用 (shiftで先読み防止)
- クロスアセット特徴量 (6): 米10年金利・ドル指数・米株の前日/5日変化
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import add_indicators

# 各時間足に対して使う上位足 (GMOのinterval表記)
HTF_MAP = {
    "15min": ["1hour", "4hour"],
    "1hour": ["4hour", "1day"],
    "4hour": ["1day", "1week"],
}
# 上位足のEMA50が安定するのに必要な最低取得日数
MIN_DAYS = {"15min": 10, "30min": 20, "1hour": 40, "4hour": 200,
            "1day": 800, "1week": 1000}

MACRO_COLS = ["us10y_d1", "us10y_d5", "dxy_d1", "dxy_d5", "spx_d1", "spx_d5"]

FEATURE_LABELS = {
    "ret_1": "直近1本の変化率",
    "ret_3": "直近3本の変化率",
    "ret_6": "直近6本の変化率",
    "ret_12": "直近12本の変化率",
    "ema20_dist": "EMA20乖離",
    "ema50_dist": "EMA50乖離",
    "ema_cross": "EMAクロス (20/50)",
    "rsi14": "RSI (14)",
    "rsi_slope": "RSI傾き",
    "macd_hist_n": "MACDヒストグラム",
    "macd_slope": "MACD傾き",
    "bb_pos": "ボリンジャー位置",
    "bb_width": "ボリンジャー幅",
    "atr_norm": "ボラティリティ (ATR)",
    "candle_body": "ローソク実体",
    "candle_pos": "終値の位置 (レンジ内)",
    "hour": "時間帯",
    "dow": "曜日",
    # 一目均衡表
    "ichi_cloud_pos": "雲との位置関係",
    "ichi_cloud_thick": "雲の厚さ",
    "ichi_tenkan_kijun": "転換線と基準線の差",
    "ichi_kijun_dist": "基準線からの乖離",
    # ダイバージェンス
    "div_rsi": "RSIダイバージェンス",
    "div_macd": "MACDダイバージェンス",
    # サポート・レジスタンス
    "sr_res_dist": "直近高値までの距離",
    "sr_sup_dist": "直近安値までの距離",
    "sr_range_pos": "高値安値レンジ内の位置",
    "htf1_trend": "上位足①トレンド",
    "htf1_rsi": "上位足①RSI",
    "htf1_dist": "上位足①乖離",
    "htf2_trend": "上位足②トレンド",
    "htf2_rsi": "上位足②RSI",
    "htf2_dist": "上位足②乖離",
    "us10y_d1": "米10年金利 前日変化",
    "us10y_d5": "米10年金利 5日変化",
    "dxy_d1": "ドル指数 前日変化",
    "dxy_d5": "ドル指数 5日変化",
    "spx_d1": "米株 前日変化",
    "spx_d5": "米株 5日変化",
}
FEATURES = list(FEATURE_LABELS.keys())

# --- 特徴量セット ---------------------------------------------------------
# 環境変数 FX_FEATURE_SET でどれを使うか切り替える (既定 full)。
# 検証用に絞り込んだ組み合わせを用意し、compare_features.py で比較する。
_CORE = [  # ローソク形状・ボラティリティ・短期リターン (重要度上位)
    "candle_pos", "candle_body", "atr_norm", "bb_width", "bb_pos",
    "ret_1", "ret_3", "ret_6", "ret_12",
]
_TREND = ["ema20_dist", "ema50_dist", "ema_cross", "htf1_trend"]
_OSC = ["rsi14", "rsi_slope", "macd_hist_n", "macd_slope"]
_TIME = ["hour"]
_ICHI = ["ichi_cloud_pos", "ichi_cloud_thick", "ichi_tenkan_kijun", "ichi_kijun_dist"]
_DIV = ["div_rsi", "div_macd"]
_SR = ["sr_res_dist", "sr_sup_dist", "sr_range_pos"]

FEATURE_SETS: dict[str, list[str]] = {
    "full": FEATURES,                                   # 39列 (現行)
    "lean": _CORE + _TREND + _OSC + _TIME,              # 18列 実績のある定番のみ
    "core": _CORE + ["ema_cross", "rsi14", "macd_hist_n", "hour"],  # 13列 最小構成
    "lean_ichi": _CORE + _TREND + _OSC + _TIME + _ICHI,  # 22列 定番+一目
    "lean_sr": _CORE + _TREND + _OSC + _TIME + _SR + _DIV,  # 23列 定番+サポレジ+乖離
    "no_macro": [f for f in FEATURES                    # 32列 マクロと曜日だけ除外
                 if not f.startswith(("us10y", "dxy", "spx")) and f != "dow"],
}


def active_features() -> list[str]:
    """今使う特徴量。環境変数 FX_FEATURE_SET で切り替え。"""
    import os
    name = os.environ.get("FX_FEATURE_SET", "full")
    return FEATURE_SETS.get(name, FEATURES)

INTERVAL_JA = {
    "1min": "1分足", "5min": "5分足", "10min": "10分足", "15min": "15分足",
    "30min": "30分足", "1hour": "1時間足", "4hour": "4時間足",
    "8hour": "8時間足", "12hour": "12時間足",
    "1day": "日足", "1week": "週足", "1month": "月足",
}


def feature_label(feature: str, interval: str) -> str:
    """表示用ラベル。上位足特徴量は実際の時間足名で表示する。"""
    if feature.startswith("htf"):
        htfs = HTF_MAP.get(interval, [])
        idx = 0 if feature.startswith("htf1") else 1
        if idx < len(htfs):
            suffix = {"trend": "トレンド", "rsi": "RSI", "dist": "乖離"}[feature.split("_")[1]]
            return f"{INTERVAL_JA.get(htfs[idx], htfs[idx])}{suffix}"
    return FEATURE_LABELS[feature]


def _base_features(df: pd.DataFrame) -> pd.DataFrame:
    d = add_indicators(df)
    c = d["close"]
    atr = d["atr14"].replace(0, np.nan)
    f = pd.DataFrame(index=d.index)
    f["ret_1"] = c.pct_change(1)
    f["ret_3"] = c.pct_change(3)
    f["ret_6"] = c.pct_change(6)
    f["ret_12"] = c.pct_change(12)
    f["ema20_dist"] = (c - d["ema20"]) / atr
    f["ema50_dist"] = (c - d["ema50"]) / atr
    f["ema_cross"] = (d["ema20"] - d["ema50"]) / atr
    f["rsi14"] = d["rsi14"]
    f["rsi_slope"] = d["rsi14"].diff(3)
    f["macd_hist_n"] = d["macd_hist"] / atr
    f["macd_slope"] = d["macd"].diff(3) / atr
    width = (d["bb_upper"] - d["bb_lower"]).replace(0, np.nan)
    f["bb_pos"] = (c - d["bb_lower"]) / width
    f["bb_width"] = width / c
    f["atr_norm"] = atr / c
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    f["candle_body"] = (c - d["open"]) / rng
    f["candle_pos"] = (c - d["low"]) / rng
    f["hour"] = d.index.hour
    f["dow"] = d.index.dayofweek

    # --- 一目均衡表: 雲との位置関係 (ATRで正規化して通貨ペア間で比較可能に) ---
    cloud_hi = pd.concat([d["senkou_a"], d["senkou_b"]], axis=1).max(axis=1)
    cloud_lo = pd.concat([d["senkou_a"], d["senkou_b"]], axis=1).min(axis=1)
    # 雲の上なら正、下なら負、雲の中なら0付近
    f["ichi_cloud_pos"] = np.where(
        c > cloud_hi, (c - cloud_hi) / atr,
        np.where(c < cloud_lo, (c - cloud_lo) / atr, 0.0))
    f["ichi_cloud_thick"] = (cloud_hi - cloud_lo) / atr   # 厚い雲=抜けにくい
    f["ichi_tenkan_kijun"] = (d["tenkan"] - d["kijun"]) / atr
    f["ichi_kijun_dist"] = (c - d["kijun"]) / atr

    # --- ダイバージェンス ---
    f["div_rsi"] = d["div_rsi"]
    f["div_macd"] = d["div_macd"]

    # --- サポート・レジスタンス (直近の確定スイング) ---
    f["sr_res_dist"] = (d["swing_high"] - c) / atr    # 上の抵抗までの距離
    f["sr_sup_dist"] = (c - d["swing_low"]) / atr     # 下の支持までの距離
    sr_range = (d["swing_high"] - d["swing_low"]).replace(0, np.nan)
    f["sr_range_pos"] = (c - d["swing_low"]) / sr_range   # 0=安値付近, 1=高値付近
    return f


def _htf_features(base_index, htf_df: pd.DataFrame | None, prefix: str) -> pd.DataFrame:
    """上位足のトレンド特徴量。shift(1)で確定足のみ使い、先読みを防ぐ。"""
    cols = [f"{prefix}_trend", f"{prefix}_rsi", f"{prefix}_dist"]
    if htf_df is None or len(htf_df) < 60:
        return pd.DataFrame(0.0, index=base_index, columns=cols)
    d = add_indicators(htf_df)
    atr = d["atr14"].replace(0, np.nan)
    f = pd.DataFrame(index=d.index)
    f[cols[0]] = (d["ema20"] - d["ema50"]) / atr
    f[cols[1]] = d["rsi14"]
    f[cols[2]] = (d["close"] - d["ema20"]) / atr
    f = f.shift(1)  # 進行中の上位足は未確定なので1本前を使う
    return f.reindex(base_index, method="ffill").fillna(0.0)


def _macro_features(base_index, macro_df: pd.DataFrame | None) -> pd.DataFrame:
    if macro_df is None or macro_df.empty:
        return pd.DataFrame(0.0, index=base_index, columns=MACRO_COLS)
    m = macro_df.reindex(columns=MACRO_COLS)
    return m.reindex(base_index, method="ffill").fillna(0.0)


def build_features(df: pd.DataFrame, extras: dict | None = None) -> pd.DataFrame:
    """OHLC DataFrame (+上位足/マクロ) -> 特徴量 DataFrame。

    extras: {"htf1": df|None, "htf2": df|None, "macro": df|None}
    無い場合は0埋め(オフラインでも動作する)。
    """
    extras = extras or {}
    parts = [
        _base_features(df),
        _htf_features(df.index, extras.get("htf1"), "htf1"),
        _htf_features(df.index, extras.get("htf2"), "htf2"),
        _macro_features(df.index, extras.get("macro")),
    ]
    return pd.concat(parts, axis=1)[active_features()]


def build_labels(df: pd.DataFrame, atr_filter: float = 0.05):
    """次の足の方向ラベル。微小変動(ノイズ)は学習から除外する。"""
    d = add_indicators(df)
    next_ret = d["close"].shift(-1) - d["close"]
    label = (next_ret > 0).astype(int)
    train_mask = next_ret.abs() > (d["atr14"] * atr_filter)
    label[next_ret.isna()] = -1
    return label, train_mask


def dataset(df: pd.DataFrame, extras: dict | None = None, atr_filter: float = 0.05):
    X = build_features(df, extras)
    y, mask = build_labels(df, atr_filter)
    valid = X.notna().all(axis=1) & (y >= 0) & mask
    return X[valid], y[valid]
