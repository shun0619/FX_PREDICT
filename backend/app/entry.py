"""エントリー適正度スコアと損切り(SL)・利確(TP)目安の算出。

スコア(0-100)の内訳:
  - AI信頼度      40点: 上昇/下落確率の50%からの乖離(信頼度60で満点)
  - バンド位置    20点: 予測方向に対して有利な位置か(UPなら下バンド寄りが高得点)
  - RSI位置       20点: 予測方向に対して過熱していないか
  - 過去の実績    20点: 似た確率帯で出した過去予測の実測的中率(10件未満は中立)

SL/TP:
  - SL = 終値 ∓ 1.5 x ATR14
  - TP = 直近96本のスイング高値/安値が妥当な範囲(0.5〜3 ATR)にあればそこ、
         無ければ 1.5 x ATR x 信頼度係数(0.75〜1.25)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .gmo import pip_size
from .indicators import add_indicators


def sl_mult() -> float:
    """初期SLのATR倍率。設定(sl_atr_mult)を全画面で共通に使う。"""
    try:
        return float(config.load().get("sl_atr_mult", 1.5))
    except Exception:
        return 1.5


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def tp_ratio(conf: float) -> float:
    """利確幅を損切り幅の何倍にするか。信頼度が高いほど利確を遠くに置く。

    損切り幅そのものを基準にすることで、損切りの倍率(sl_atr_mult)を
    変えても損益バランスが1.0を下回らないようにしている。
    以前はATR基準の独立した式だったため、確率75%未満だと必ず
    損益バランスが1.0を割り、自動売買が永久に発注できなくなっていた。
    """
    return 1.1 + conf / 100 * 0.6      # 信頼度0で1.1倍、100で1.7倍


def sl_tp_for(df: pd.DataFrame, up: bool, conf: float, symbol: str) -> tuple[float, float]:
    """指定方向のSL/TP。売買方向がAIの見立てと異なる場合にも対応。"""
    d = add_indicators(df)
    close = float(d["close"].iloc[-1])
    atr = float(d["atr14"].iloc[-1]) or close * 1e-4
    digits = 3 if symbol.endswith("_JPY") else 5
    sl_dist = sl_mult() * atr
    sl = close - sl_dist if up else close + sl_dist

    # 直近スイングは「損切り幅の1.0〜3.0倍」の範囲にあるときだけ採用する。
    # それより近いスイングを使うと損益バランスが1.0を割ってしまう
    look = df.tail(96)
    default_dist = sl_dist * tp_ratio(conf)
    if up:
        swing = float(look["high"].max())
        dist = swing - close
        tp = swing if sl_dist <= dist <= sl_dist * 3 else close + default_dist
    else:
        swing = float(look["low"].min())
        dist = close - swing
        tp = swing if sl_dist <= dist <= sl_dist * 3 else close - default_dist
    return round(sl, digits), round(tp, digits)


def entry_plan(df: pd.DataFrame, pred: dict, symbol: str,
               hist_acc: float | None, hist_n: int) -> dict:
    d = add_indicators(df)
    last = d.iloc[-1]
    close = float(last["close"])
    atr = float(last["atr14"]) or close * 1e-4
    rsi = float(last["rsi14"])
    width = float(last["bb_upper"] - last["bb_lower"])
    bb_pos = _clamp((close - float(last["bb_lower"])) / width) if width > 0 else 0.5

    up = pred["direction"] == "UP"
    conf = float(pred["confidence"])  # 0-100

    # --- スコア成分 (各0-1) ---
    s_ai = _clamp(conf / 60)
    s_bb = _clamp(1 - bb_pos if up else bb_pos)
    s_rsi = _clamp((70 - rsi) / 40 if up else (rsi - 30) / 40)
    if hist_n >= 10 and hist_acc is not None:
        s_hist = _clamp((hist_acc * 100 - 40) / 20)  # 的中40%で0点, 60%で満点
    else:
        s_hist = 0.5  # 実績不足は中立

    score = round(40 * s_ai + 20 * s_bb + 20 * s_rsi + 20 * s_hist)
    level = "good" if score >= 65 else "fair" if score >= 45 else "poor"

    # --- SL / TP (sl_tp_for と同じ規則。損益バランスが1.0を割らないようにする) ---
    sl_dist = sl_mult() * atr
    sl = close - sl_dist if up else close + sl_dist
    default_dist = sl_dist * tp_ratio(conf)
    look = df.tail(96)
    if up:
        swing = float(look["high"].max())
        tp = swing if sl_dist <= (swing - close) <= sl_dist * 3 else close + default_dist
    else:
        swing = float(look["low"].min())
        tp = swing if sl_dist <= (close - swing) <= sl_dist * 3 else close - default_dist
    tp_from_swing = tp == swing
    rr = abs(tp - close) / abs(close - sl) if close != sl else 0.0

    digits = 3 if symbol.endswith("_JPY") else 5
    pip = pip_size(symbol)
    return {
        "symbol": symbol,
        "direction": pred["direction"],
        "prob_up": pred["prob_up"],
        "confidence": conf,
        "bar_time": pred["bar_time"],
        "entry": round(close, digits),
        "score": int(score),
        "level": level,
        "components": [
            {"key": "ai", "label": "AI信頼度", "pct": round(s_ai * 100)},
            {"key": "bb", "label": "バンド位置", "pct": round(s_bb * 100)},
            {"key": "rsi", "label": "RSI位置", "pct": round(s_rsi * 100)},
            {"key": "hist", "label": "過去の的中実績",
             "pct": round(s_hist * 100),
             "note": f"{hist_n}件" if hist_n else "実績なし(中立)"},
        ],
        "sl": round(sl, digits),
        "tp": round(tp, digits),
        "sl_pips": round(abs(close - sl) / pip, 1),
        "tp_pips": round(abs(tp - close) / pip, 1),
        "rr": round(rr, 2),
        "tp_basis": "直近スイング" if tp_from_swing else "ATR基準",
        "atr": round(atr, digits),
        "sl_atr_mult": sl_mult(),
    }
