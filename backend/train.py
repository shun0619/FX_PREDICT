"""CLI学習スクリプト。

例:
  python train.py --symbol USD_JPY --interval 15m --days 120
  python train.py --all           # 主要ペア x 15m,1h を一括学習
"""
from __future__ import annotations

import argparse
import json

from app import gmo, model

TRAIN_DAYS = {"15m": 120, "1h": 365, "4h": 900}


def run(symbol: str, tf: str, days: int | None):
    days = days or TRAIN_DAYS[tf]
    print(f"== {symbol} {tf} ({days}日分) 取得中...")
    df = gmo.drop_incomplete(
        gmo.fetch_klines(symbol, gmo.INTERVAL_MAP[tf], days), gmo.INTERVAL_MAP[tf])
    print(f"   {len(df)}本取得 ({df.index[0]} - {df.index[-1]})")
    extras = model.load_extras(symbol, gmo.INTERVAL_MAP[tf], days, use_cache=False)
    meta = model.train(df, symbol, gmo.INTERVAL_MAP[tf], extras=extras)
    print(json.dumps({k: meta[k] for k in
                      ("train_rows", "wf_mean_accuracy", "wf_mean_auc",
                       "baseline_accuracy")}, indent=2))
    wf = meta.get("wf_mean_accuracy") or 0
    base = meta.get("baseline_accuracy") or 0
    if wf <= base:
        print("   ⚠ ウォークフォワード精度がベースライン以下。"
              "このモデルにエッジはありません。参考程度に。")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="USD_JPY")
    p.add_argument("--interval", default="15m", choices=list(gmo.INTERVAL_MAP))
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--all", action="store_true")
    a = p.parse_args()
    if a.all:
        for s in gmo.SYMBOLS:
            for tf in ("15m", "1h"):
                try:
                    run(s, tf, None)
                except Exception as e:
                    print(f"   失敗: {e}")
    else:
        run(a.symbol, a.interval, a.days)
