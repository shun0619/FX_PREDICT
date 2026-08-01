"""クロスアセット指標 (FRED 無料CSV、APIキー不要)。

- DGS10:    米10年国債利回り
- DTWEXBGS: ドル指数 (広義実効レート)
- SP500:    S&P500
日次データのため「前日までの確定値」だけを使う(shiftで先読み防止)。
6時間ディスクキャッシュ。取得失敗時は古いキャッシュを使い、無ければ欠損扱い。
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import httpx
import pandas as pd

FRED = {"us10y": "DGS10", "dxy": "DTWEXBGS", "spx": "SP500"}
CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "macro"
_TTL = 6 * 3600


def _fetch_series(name: str) -> pd.Series:
    fid = FRED[name]
    f = CACHE / f"{fid}.csv"
    stale = not f.exists() or (time.time() - f.stat().st_mtime > _TTL)
    if stale:
        try:
            r = httpx.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fid}",
                          timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (personal fx tool)"})
            r.raise_for_status()
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(r.text, encoding="utf-8")
        except Exception:
            if not f.exists():
                raise
    df = pd.read_csv(io.StringIO(f.read_text(encoding="utf-8")))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["value"].dropna()


def features_for(symbol: str | None = None) -> pd.DataFrame | None:
    """日次のクロスアセット特徴量。取れなかった系列は欠損(呼び出し側で0埋め)。"""
    out: dict[str, pd.Series] = {}
    for name in FRED:
        try:
            s = _fetch_series(name).tail(400)
        except Exception:
            continue
        if name == "spx":
            out[f"{name}_d1"] = s.pct_change(1) * 100
            out[f"{name}_d5"] = s.pct_change(5) * 100
        else:
            out[f"{name}_d1"] = s.diff(1)
            out[f"{name}_d5"] = s.diff(5)
    if not out:
        return None
    # shift(1): その日の値はその日の取引中には未確定なので、前日値を使う
    return pd.DataFrame(out).shift(1)
