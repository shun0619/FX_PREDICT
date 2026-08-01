"""LightGBM 学習・推論。ウォークフォワード検証付き。

モデルは backend/models/{symbol}_{interval}.txt に保存。
メタ情報(検証成績・学習日時)は同名 .json。
後で LSTM に差し替える場合はこのモジュールの train/predict を置き換えるだけでよい。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import gmo, macro
from .features import (FEATURE_LABELS, HTF_MAP, MIN_DAYS, active_features,
                       build_features, dataset, feature_label)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"


# 上位足とマクロの短期キャッシュ。
# 画面は数秒おきにポーリングするが、上位足(1時間足・日足など)は
# その間に変化しないため、毎回組み立て直すのは無駄。
_extras_cache: dict[tuple, tuple[float, dict]] = {}
_EXTRAS_TTL = 60.0


def load_extras(symbol: str, interval: str, days: int,
                use_cache: bool = True) -> dict:
    """上位足OHLCとマクロ特徴量を取得(失敗しても None で続行)。

    use_cache=False は学習時に使う(必ず最新を取り直す)。
    """
    key = (symbol, interval, days)
    if use_cache:
        hit = _extras_cache.get(key)
        if hit and time.time() - hit[0] < _EXTRAS_TTL:
            return hit[1]

    extras: dict = {}
    for i, htf in enumerate(HTF_MAP.get(interval, [])[:2], 1):
        try:
            d = max(days, MIN_DAYS.get(htf, days))
            extras[f"htf{i}"] = gmo.fetch_klines(symbol, htf, d)
        except Exception:
            extras[f"htf{i}"] = None
    try:
        extras["macro"] = macro.features_for(symbol)
    except Exception:
        extras["macro"] = None

    if use_cache:
        _extras_cache[key] = (time.time(), extras)
    return extras

PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 42,
}


def _path(symbol: str, interval: str) -> Path:
    return MODEL_DIR / f"{symbol}_{interval}.txt"


def _oos_path(symbol: str, interval: str) -> Path:
    """ウォークフォワード予測(out-of-sample)の保存先。"""
    return MODEL_DIR / f"{symbol}_{interval}_oos.csv"


def load_oos(symbol: str, interval: str) -> pd.Series | None:
    """学習時に記録した「未学習データに対する予測」を読む。無ければ None。"""
    p = _oos_path(symbol, interval)
    if not p.exists():
        return None
    s = pd.read_csv(p, index_col=0)
    s.index = pd.to_datetime(s.index, utc=True)
    return s["prob_up"]


def train(df: pd.DataFrame, symbol: str, interval: str, n_folds: int = 4,
          extras: dict | None = None) -> dict:
    """ウォークフォワード検証 -> 全データで最終学習 -> 保存。"""
    X, y = dataset(df, extras)
    if len(X) < 300:
        raise ValueError(f"学習データ不足: {len(X)}行 (300行以上必要)。daysを増やしてください")

    # --- walk-forward: 時系列順に拡大窓で検証 ---
    n = len(X)
    fold_metrics = []
    oos: list[pd.Series] = []   # 各フォールドの「未学習データに対する予測」
    edges = np.linspace(0.4, 1.0, n_folds + 1)
    for i in range(n_folds):
        tr_end = int(n * edges[i])
        te_end = int(n * edges[i + 1])
        Xtr, ytr = X.iloc[:tr_end], y.iloc[:tr_end]
        Xte, yte = X.iloc[tr_end:te_end], y.iloc[tr_end:te_end]
        if len(Xte) < 20:
            continue
        m = lgb.train(PARAMS, lgb.Dataset(Xtr, ytr), num_boost_round=300,
                      valid_sets=[lgb.Dataset(Xte, yte)],
                      callbacks=[lgb.early_stopping(30, verbose=False)])
        p = m.predict(Xte)
        oos.append(pd.Series(p, index=Xte.index))
        acc = float(((p > 0.5).astype(int) == yte.values).mean())
        # AUC
        order = np.argsort(p)
        ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
        pos = yte.values == 1
        n1, n0 = pos.sum(), (~pos).sum()
        auc = float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else 0.5
        fold_metrics.append({"fold": i + 1, "test_rows": len(Xte),
                             "accuracy": round(acc, 4), "auc": round(auc, 4)})

    baseline = float(max(y.mean(), 1 - y.mean()))  # 常に多数派を予測した場合の精度

    # --- 最終モデル: 全データで学習 ---
    booster = lgb.train(PARAMS, lgb.Dataset(X, y), num_boost_round=200)
    p = _path(symbol, interval)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 一時ファイルに書いてから置換する。書き込み途中のファイルを
    # 予測側が読んで壊れたモデルを掴むのを防ぐ
    tmp = p.with_suffix(".txt.tmp")
    booster.save_model(str(tmp))
    tmp.replace(p)

    # ウォークフォワード予測(未学習データに対する予測)を保存する。
    # バックテストはこれを使う。全データで学習したモデルで過去を予測すると
    # 答えを知っている問題を解くことになり、成績が実力より大幅に高く出るため。
    if oos:
        s = pd.concat(oos).sort_index()
        s.name = "prob_up"
        oos_tmp = _oos_path(symbol, interval).with_suffix(".csv.tmp")
        s.to_csv(oos_tmp, header=True)
        oos_tmp.replace(_oos_path(symbol, interval))

    meta = {
        "symbol": symbol, "interval": interval,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_rows": int(len(X)),
        "data_from": str(X.index[0]), "data_to": str(X.index[-1]),
        "walk_forward": fold_metrics,
        "wf_mean_accuracy": round(float(np.mean([f["accuracy"] for f in fold_metrics])), 4) if fold_metrics else None,
        "wf_mean_auc": round(float(np.mean([f["auc"] for f in fold_metrics])), 4) if fold_metrics else None,
        "baseline_accuracy": round(baseline, 4),
        "feature_importance": dict(zip(active_features(),
                                       [int(v) for v in booster.feature_importance()])),
    }
    _path(symbol, interval).with_suffix(".json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    return meta


_cache: dict[str, tuple[float, lgb.Booster, dict]] = {}


def load(symbol: str, interval: str) -> tuple[lgb.Booster, dict] | None:
    """学習済みモデルを読む。ファイルの更新時刻でキャッシュを無効化するので
    再学習した直後は自動で新しいモデルに切り替わる。"""
    p = _path(symbol, interval)
    if not p.exists():
        return None
    key = str(p)
    mtime = p.stat().st_mtime
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    booster = lgb.Booster(model_file=key)
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    _cache[key] = (mtime, booster, meta)
    return booster, meta


def predict(df: pd.DataFrame, symbol: str, interval: str,
            extras: dict | None = None) -> dict:
    """最新足に対する上昇確率と、特徴量ごとの寄与(予測根拠)を返す。"""
    loaded = load(symbol, interval)
    if loaded is None:
        raise FileNotFoundError(
            f"モデル未学習: {symbol} {interval}。POST /api/train で学習してください")
    booster, meta = loaded
    if booster.num_feature() != len(active_features()):
        raise FileNotFoundError(
            "特徴量が更新されたため旧モデルは使えません。モデル再学習をしてください")
    X = build_features(df, extras)
    row = X.iloc[[-1]].fillna(0)
    prob_up = float(booster.predict(row)[0])

    # SHAP風の寄与 (pred_contrib): 正=上昇方向, 負=下落方向
    contrib = booster.predict(row, pred_contrib=True)[0][:-1]
    total = np.abs(contrib).sum() or 1.0
    items = sorted(
        ({"feature": f, "label": feature_label(f, interval),
          "contribution": round(float(c) / total * 100, 1)}
         for f, c in zip(active_features(), contrib)),
        key=lambda x: -abs(x["contribution"]))[:6]

    return {
        "prob_up": round(prob_up, 4),
        "prob_down": round(1 - prob_up, 4),
        "direction": "UP" if prob_up >= 0.5 else "DOWN",
        "confidence": round(abs(prob_up - 0.5) * 200, 1),  # 0-100
        "bar_time": str(df.index[-1]),
        "close": float(df["close"].iloc[-1]),
        "basis": items,
        "model": {
            "trained_at": meta["trained_at"],
            "wf_mean_accuracy": meta.get("wf_mean_accuracy"),
            "wf_mean_auc": meta.get("wf_mean_auc"),
            "baseline_accuracy": meta.get("baseline_accuracy"),
        },
    }


def predict_series(df: pd.DataFrame, symbol: str, interval: str,
                   extras: dict | None = None) -> pd.Series:
    """全行に対する上昇確率(バックテスト用)。"""
    loaded = load(symbol, interval)
    if loaded is None:
        raise FileNotFoundError(f"モデル未学習: {symbol} {interval}")
    booster, _ = loaded
    if booster.num_feature() != len(active_features()):
        raise FileNotFoundError(
            "特徴量が更新されたため旧モデルは使えません。モデル再学習をしてください")
    X = build_features(df, extras)
    valid = X.notna().all(axis=1)
    probs = pd.Series(np.nan, index=X.index)
    if valid.any():
        probs[valid] = booster.predict(X[valid])
    return probs
