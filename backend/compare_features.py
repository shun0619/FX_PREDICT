"""特徴量セットの比較検証。

「特徴量を絞るとノイズが減って精度が上がるのか」を数字で確かめる。

比較する組み合わせ (features.py の FEATURE_SETS で定義):
    full       39列  現行のすべて
    no_macro   32列  マクロ指標と曜日だけ除外 (重要度が低かったもの)
    lean_sr    23列  定番 + サポレジ + ダイバージェンス
    lean_ichi  22列  定番 + 一目均衡表
    lean       18列  実績のある定番テクニカルのみ
    core       13列  最小構成

使い方 (backendフォルダで):
    python compare_features.py                        # USD_JPY 1h
    python compare_features.py --symbol EUR_USD
    python compare_features.py --all                  # 主要3ペア
    python compare_features.py --seeds 3              # 乱数を3通り試して平均

【重要】乱数の初期値だけで結果は1%程度ぶれる。1回の比較で最良を選ぶと
「たまたま当たり」を拾ってしまうため、--seeds 3 以上での判断を推奨。
"""
from __future__ import annotations

import argparse
import os
import time

from app import features as F
from app import gmo, model

SETS = ["full", "no_macro", "lean_sr", "lean_ichi", "lean", "core"]
SYMBOLS = ["USD_JPY", "EUR_USD", "GBP_USD"]


def run_one(symbol: str, tf: str, days: int, setname: str, seed: int) -> dict | None:
    os.environ["FX_FEATURE_SET"] = setname
    model.PARAMS["seed"] = seed
    model.PARAMS["bagging_seed"] = seed
    model.PARAMS["feature_fraction_seed"] = seed

    iv = gmo.INTERVAL_MAP[tf]
    df = gmo.drop_incomplete(gmo.fetch_klines(symbol, iv, days), iv)
    if len(df) < 500:
        return None
    extras = model.load_extras(symbol, iv, days)

    orig = model.MODEL_DIR
    tmp = orig / "_cmp_feat"
    tmp.mkdir(parents=True, exist_ok=True)
    model.MODEL_DIR = tmp
    try:
        meta = model.train(df, symbol, iv, extras=extras)
    except Exception as e:
        print(f"    学習失敗: {e}")
        return None
    finally:
        model.MODEL_DIR = orig

    wf = meta.get("wf_mean_accuracy") or 0
    base = meta.get("baseline_accuracy") or 0
    return {"wf": wf, "base": base, "edge": wf - base,
            "auc": meta.get("wf_mean_auc") or 0,
            "n_features": len(F.FEATURE_SETS[setname]),
            "folds": [f["accuracy"] for f in meta.get("walk_forward", [])]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="USD_JPY")
    p.add_argument("--interval", default="1h", choices=list(gmo.INTERVAL_MAP))
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--seeds", type=int, default=3, help="試す乱数の数")
    p.add_argument("--sets", nargs="+", default=SETS)
    p.add_argument("--all", action="store_true")
    a = p.parse_args()

    targets = SYMBOLS if a.all else [a.symbol]
    grand: dict[str, list[float]] = {s: [] for s in a.sets}

    for symbol in targets:
        print(f"\n{'='*78}")
        print(f"  {symbol} {a.interval} — 特徴量セット比較 "
              f"({a.days}日 / 乱数{a.seeds}通りの平均)")
        print(f"{'='*78}")
        print(f"{'セット':<11}{'列数':>5}{'WF精度':>9}{'基準':>8}{'エッジ':>9}"
              f"{'ぶれ幅':>8}{'AUC':>8}  区間別")
        print("-" * 78)

        rows = []
        for setname in a.sets:
            edges, wfs, aucs, bases, allfolds = [], [], [], [], []
            t0 = time.time()
            for seed in range(42, 42 + a.seeds):
                r = run_one(symbol, a.interval, a.days, setname, seed)
                if r:
                    edges.append(r["edge"]); wfs.append(r["wf"])
                    aucs.append(r["auc"]); bases.append(r["base"])
                    allfolds.append(r["folds"])
            if not edges:
                continue
            avg_edge = sum(edges) / len(edges)
            spread = (max(edges) - min(edges)) * 100   # 乱数によるぶれ
            n_feat = F.FEATURE_SETS[setname].__len__()
            folds = [sum(f[i] for f in allfolds) / len(allfolds)
                     for i in range(len(allfolds[0]))] if allfolds else []
            fold_txt = " ".join(f"{x*100:.0f}" for x in folds)
            rows.append({"set": setname, "edge": avg_edge, "n": n_feat,
                         "wf": sum(wfs)/len(wfs), "auc": sum(aucs)/len(aucs),
                         "spread": spread})
            grand[setname].append(avg_edge)
            print(f"{setname:<11}{n_feat:>5}{sum(wfs)/len(wfs)*100:8.2f}%"
                  f"{sum(bases)/len(bases)*100:7.2f}%{avg_edge*100:+8.2f}%"
                  f"{spread:7.2f}%{sum(aucs)/len(aucs):8.4f}  {fold_txt}"
                  f"  ({time.time()-t0:.0f}秒)")

        if rows:
            best = max(rows, key=lambda x: x["edge"])
            full = next((r for r in rows if r["set"] == "full"), None)
            print(f"\n  最良: {best['set']} ({best['n']}列) エッジ {best['edge']*100:+.2f}%")
            if full and best["set"] != "full":
                diff = (best["edge"] - full["edge"]) * 100
                verdict = ("絞った効果あり" if diff > best["spread"] else
                           "乱数のぶれ幅と同程度 = 誤差の可能性が高い")
                print(f"  現行(full)との差: {diff:+.2f}%  → {verdict}")
                print(f"  (乱数だけで {best['spread']:.2f}% ぶれるので、"
                      f"それを超えない差は信用しないこと)")

    if len(targets) > 1:
        print(f"\n{'='*78}\n  全ペア平均のエッジ\n{'='*78}")
        for s in a.sets:
            v = grand[s]
            if v:
                print(f"  {s:<11}{F.FEATURE_SETS[s].__len__():>4}列  "
                      f"{sum(v)/len(v)*100:+.2f}%")
        print("\n  → 全ペアで一貫して勝っているセットがあれば、それは本物の改善")

    print("\n採用するには環境変数で切り替えて再学習:")
    print("  FX_FEATURE_SET=lean python train.py --symbol USD_JPY --interval 1h")
    print("  (uvicorn 起動時にも同じ環境変数を設定すること)")


if __name__ == "__main__":
    main()
