"""GMOコイン 外国為替FX Public API クライアント(口座・APIキー不要)。

- ticker:  GET /public/v1/ticker
- klines:  GET /public/v1/klines?symbol=&priceType=&interval=&date=
  interval: 1min 5min 10min 15min 30min 1hour -> date=YYYYMMDD (JST6:00更新, 20231028以降)
            4hour 8hour 12hour 1day 1week 1month -> date=YYYY
過去分は data/cache/ にJSONキャッシュして再取得を避ける。
FX_SYNTHETIC=1 でオフライン用の合成データを生成(開発・テスト用)。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

BASE = "https://forex-api.coin.z.com/public"
JST = timezone(timedelta(hours=9))
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

INTRADAY = {"1min", "5min", "10min", "15min", "30min", "1hour"}
YEARLY = {"4hour", "8hour", "12hour", "1day", "1week", "1month"}

# フロントの時間足表記 -> GMO interval
# フロントの時間足表記 -> GMO interval
# 1分足・5分足は実測でエッジがほぼ無く(スプレッドで消える水準)、
# 値動きも荒いため対象外にしている。必要になったらここに戻すだけで復活できる。
INTERVAL_MAP = {
    "15m": "15min", "1h": "1hour", "4h": "4hour",
}

# 分析対象の通貨ペア。流動性が高くスプレッドの狭い主要3ペアに絞っている。
# ペアを増やすとモデルの学習・検証・実績の蓄積がそれぞれ必要になるため、
# 実際に使うものだけを対象にする方が検証が早く進む。
SYMBOLS = ["USD_JPY", "EUR_USD", "GBP_USD"]

SYNTHETIC = os.environ.get("FX_SYNTHETIC", "0") == "1"

_INTERVAL_SEC = {
    "1min": 60, "5min": 300, "10min": 600, "15min": 900, "30min": 1800,
    "1hour": 3600, "4hour": 14400, "8hour": 28800, "12hour": 43200,
    "1day": 86400, "1week": 604800, "1month": 2592000,
}


def pip_size(symbol: str) -> float:
    return 0.01 if symbol.endswith("_JPY") else 0.0001


def _get(path: str, params: dict | None = None) -> dict:
    for attempt in range(3):
        try:
            r = httpx.get(BASE + path, params=params, timeout=15)
            r.raise_for_status()
            d = r.json()
            if d.get("status") != 0:
                raise RuntimeError(f"GMO API error: {d}")
            return d
        except (httpx.HTTPError, RuntimeError):
            if attempt == 2:
                raise
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def get_ticker(symbol: str | None = None) -> list[dict]:
    if SYNTHETIC:
        df = fetch_klines("USD_JPY", "15min", days=1)
        last = float(df.iloc[-1]["close"])
        return [{"symbol": symbol or "USD_JPY", "bid": f"{last:.3f}",
                 "ask": f"{last + 0.003:.3f}",
                 "timestamp": datetime.now(timezone.utc).isoformat(),
                 "status": "OPEN"}]
    data = _get("/v1/ticker")["data"]
    if symbol:
        data = [d for d in data if d["symbol"] == symbol]
    return data


# メモリキャッシュ: 確定日は永続、当日分は短TTL
_mem_final: dict[tuple, list] = {}
_mem_today: dict[tuple, tuple[float, list]] = {}
_TODAY_TTL = 8.0  # 秒。チャートと予測が同時ポーリングしても実リクエストは1回


def _fetch_klines_raw(symbol: str, interval: str, date: str,
                      price_type: str = "BID") -> list[dict]:
    """1リクエスト分(1日 or 1年)のKLineを取得。3段キャッシュ(メモリ/ディスク/API)。"""
    mkey = (symbol, interval, price_type, date)
    today_jst = datetime.now(JST).strftime("%Y%m%d")
    this_year = datetime.now(JST).strftime("%Y")
    is_final = (len(date) == 8 and date < today_jst) or (len(date) == 4 and date < this_year)

    if is_final:
        if mkey in _mem_final:
            return _mem_final[mkey]
        key = CACHE_DIR / symbol / interval / f"{price_type}_{date}.json"
        if key.exists():
            data = json.loads(key.read_text())
            _mem_final[mkey] = data
            return data
        data = _get("/v1/klines", {"symbol": symbol, "priceType": price_type,
                                   "interval": interval, "date": date})["data"]
        # 休場日の空リストもキャッシュする(毎回の再リクエストを防ぐ)
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps(data))
        _mem_final[mkey] = data
        return data

    # 当日(未確定)分: 短TTLのメモリキャッシュ
    hit = _mem_today.get(mkey)
    if hit and time.time() - hit[0] < _TODAY_TTL:
        return hit[1]
    data = _get("/v1/klines", {"symbol": symbol, "priceType": price_type,
                               "interval": interval, "date": date})["data"]
    _mem_today[mkey] = (time.time(), data)
    return data


def fetch_klines(symbol: str, interval: str, days: int = 30,
                 price_type: str = "BID") -> pd.DataFrame:
    """直近days日分のOHLCをDataFrameで返す(index: UTC datetime)。"""
    if SYNTHETIC:
        return _synthetic(symbol, interval, days)
    rows: list[dict] = []
    now = datetime.now(JST)
    if interval in INTRADAY:
        # JST 6:00 が日付境界。当日分を含めて days+1 日分をさかのぼる
        dates = [(now - timedelta(days=i)).strftime("%Y%m%d")
                 for i in range(days, -1, -1)]
        dates = [d for d in dates if d >= "20231028"]

        def _one(date: str) -> list[dict]:
            try:
                return _fetch_klines_raw(symbol, interval, date, price_type)
            except Exception:
                return []  # 休場日など

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for chunk in ex.map(_one, dates):
                rows += chunk
    else:
        years = {(now - timedelta(days=i)).year for i in (0, days)}
        for y in sorted(years):
            try:
                rows += _fetch_klines_raw(symbol, interval, str(y), price_type)
            except Exception:
                continue
    if not rows:
        raise RuntimeError(f"no kline data for {symbol} {interval}")
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["openTime"].astype(np.int64), unit="ms", utc=True)
    df = df.drop_duplicates("time").set_index("time").sort_index()
    df = df[["open", "high", "low", "close"]]
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    return df[df.index >= cutoff]


def drop_incomplete(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """進行中(未確定)の最終足を除外する。

    予測・採点は必ず確定足で行うこと。形成中の足を使うと
    学習時と条件が変わり、正誤判定も1本ズレる。
    """
    if df.empty:
        return df
    sec = _INTERVAL_SEC[interval]
    bar_end = df.index[-1] + pd.Timedelta(seconds=sec)
    if bar_end > pd.Timestamp.now(tz="UTC"):
        return df.iloc[:-1]
    return df


def _synthetic(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """オフライン開発用の合成OHLC(トレンド+平均回帰+ノイズ)。"""
    sec = _INTERVAL_SEC[interval]
    n = max(200, int(days * 86400 / sec * 5 / 7))
    rng = np.random.default_rng(abs(hash(f"{symbol}{interval}")) % (2**32))
    base = 150.0 if symbol.endswith("_JPY") else 1.09
    scale = base * 0.0004 * np.sqrt(sec / 300)
    trend = np.cumsum(rng.normal(0, 0.15, n)) * scale
    noise = rng.normal(0, 1, n) * scale
    close = base + trend + np.convolve(noise, np.ones(5) / 5, "same")
    open_ = np.roll(close, 1); open_[0] = close[0]
    spread_hl = np.abs(rng.normal(0, scale, n)) + scale * 0.3
    high = np.maximum(open_, close) + spread_hl
    low = np.minimum(open_, close) - spread_hl
    end = pd.Timestamp.now(tz="UTC").floor(f"{sec}s")
    idx = pd.date_range(end=end, periods=n, freq=f"{sec}s")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close},
                        index=idx)
