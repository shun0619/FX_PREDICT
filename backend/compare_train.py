"""学習期間の比較検証。

同じ特徴量・同じ検証方法で、学習に使う期間だけを変えて比較する。
「過去データを増やせば精度が上がるのか」を数字で確かめるためのスクリプト。

使い方 (backendフォルダで):
    python compare_train.py                      # USD_JPY 1h を 1年/2年/3年で比較
    python compare_train.py --symbol EUR_USD
    python compare_train.py --days 365 730 1095 1460
    python compare_train.py --all                # 主要3ペアをまとめて

注意: 結果を採用したい期間が決まったら、その日数で改めて学習し直すこと
      (このスクリプトはモデルを保存せず、比較だけを行う)。
"""
from __future__ import annotations

import argparse
import json
import time

from app import gmo, model

SYMBOLS = ["USD_JPY", "EUR_USD", "GBP_USD"]


def run_one(symbol: str, tf: str, days: int) -> dict | None:
    iv = gmo.INTERVAL_MAP[tf]
    t0 = time.time()
    try:
        df = gmo.drop_incomplete(gmo.fetch_klines(symbol, iv, days), iv)
    except Exception as e:
        print(f"    データ取得失敗: {e}")
        return None
    if len(df) < 500:
        print(f"    データ不足 ({len(df)}本)")
        return None
    extras = model.load_extras(symbol, iv, days)

    # 保存されている本番モデルを壊さないよう、一時ディレクトリに書く
    orig_dir = model.MODEL_DIR
    tmp_dir = orig_dir / "_compare_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    model.MODEL_DIR = tmp_dir
    try:
        meta = model.train(df, symbol, iv, extras=extras)
    except Exception as e:
        print(f"    学習失敗: {e}")
        return None
    finally:
        model.MODEL_DIR = orig_dir

    wf = meta.get("wf_mean_accuracy") or 0
    base = meta.get("baseline_accuracy") or 0
    return {
        "days": days, "bars": len(df), "rows": meta["train_rows"],
        "from": meta["data_from"][:10], "to": meta["data_to"][:10],
        "wf": wf, "base": base, "edge": wf - base,
        "auc": meta.get("wf_mean_auc") or 0,
        "folds": meta.get("walk_forward", []),
        "sec": time.time() - t0,
    }


def report(symbol: str, tf: str, results: list[dict]) -> None:
    print(f"\n{'='*78}")
    print(f"  {symbol} {tf}  — 学習期間別の比較")
    print(f"{'='*78}")
    print(f"{'期間':>8} {'本数':>7} {'学習行':>7} {'WF精度':>8} {'基準':>8} "
          f"{'エッジ':>8} {'AUC':>7}  データ範囲")
    print("-" * 78)
    for r in results:
        yrs = r["days"] / 365
        mark = "★" if r["edge"] == max(x["edge"] for x in results) else " "
        print(f"{yrs:6.1f}年 {r['bars']:7,} {r['rows']:7,} "
              f"{r['wf']*100:7.2f}% {r['base']*100:7.2f}% "
              f"{r['edge']*100:+7.2f}%{mark} {r['auc']:7.4f}  {r['from']}〜{r['to']}")

    best = max(results, key=lambda x: x["edge"])
    print(f"\n  最良: {best['days']/365:.1f}年 (エッジ {best['edge']*100:+.2f}%)")

    # フォールドごとのばらつき = 時期による安定性
    print(f"\n  【{best['days']/365:.1f}年】の区間ごとの精度 (安定しているか):")
    for f in best["folds"]:
        print(f"    区間{f['fold']}: 精度 {f['accuracy']*100:5.2f}%  "
              f"AUC {f['auc']:.4f}  ({f['test_rows']:,}行)")
    accs = [f["accuracy"] for f in best["folds"]]
    if accs:
        spread = (max(accs) - min(accs)) * 100
        print(f"    → 最良と最悪の差 {spread:.1f}ポイント "
              f"({'安定' if spread < 5 else '時期によってかなり変動'})")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="USD_JPY")
    p.add_argument("--interval", default="1h", choices=list(gmo.INTERVAL_MAP))
    p.add_argument("--days", type=int, nargs="+", default=[365, 730, 1095])
    p.add_argument("--all", action="store_true")
    a = p.parse_args()

    targets = SYMBOLS if a.all else [a.symbol]
    summary = []
    for symbol in targets:
        results = []
        for d in a.days:
            print(f"\n[{symbol} {a.interval}] {d/365:.1f}年分で学習中...")
            r = run_one(symbol, a.interval, d)
            if r:
                results.append(r)
                print(f"    WF精度 {r['wf']*100:.2f}% / 基準 {r['base']*100:.2f}% "
                      f"/ エッジ {r['edge']*100:+.2f}%  ({r['sec']:.0f}秒)")
        if results:
            report(symbol, a.interval, results)
            best = max(results, key=lambda x: x["edge"])
            summary.append((symbol, best))

    if len(summary) > 1:
        print(f"\n{'='*78}\n  まとめ (各ペアの最良設定)\n{'='*78}")
        for sym, b in summary:
            print(f"  {sym:<10} {b['days']/365:.1f}年  エッジ {b['edge']*100:+.2f}%  "
                  f"AUC {b['auc']:.4f}")

    print("\n判定のしかた:")
    print("  エッジ +2%以上 ... 使える可能性あり")
    print("  エッジ  0〜+2% ... 誤差の範囲。実運用では手数料負けしやすい")
    print("  エッジ  マイナス ... そのペア・期間では予測できていない")
    print("\n採用したい期間が決まったら、その日数で本番モデルを学習し直してください:")
    print("  python train.py --symbol USD_JPY --interval 1h --days 1095")


if __name__ == "__main__":
    main()
