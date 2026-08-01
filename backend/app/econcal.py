"""経済指標カレンダー (Forex Factory 無料JSON、1時間キャッシュ)。

高重要度(High)指標の前後は方向予測が機能しないため、警告表示に使う。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
_cache: dict = {"ts": 0.0, "data": []}
_TTL = 3600  # 1時間


def fetch_events() -> list[dict]:
    if time.time() - _cache["ts"] < _TTL and _cache["data"]:
        return _cache["data"]
    events: list[dict] = []
    for url in FF_URLS:
        try:
            r = httpx.get(url, timeout=15,
                          headers={"User-Agent": "Mozilla/5.0 (personal fx tool)"})
            r.raise_for_status()
            events += r.json()
        except Exception:
            continue  # 片方失敗しても続行
    if events:
        _cache["data"] = events
        _cache["ts"] = time.time()
    return _cache["data"]


def events_for(symbol: str, hours_ahead: float = 24, hours_back: float = 2) -> list[dict]:
    """通貨ペアに関係する High/Medium 指標を時刻順で返す。

    in_window: High指標が「2時間前〜24時間後」の警戒ゾーンにあるか。
    """
    currencies = set(symbol.split("_"))
    now = datetime.now(timezone.utc)
    out = []
    for e in fetch_events():
        if e.get("country") not in currencies:
            continue
        impact = e.get("impact")
        if impact not in ("High", "Medium"):
            continue
        try:
            t = datetime.fromisoformat(e["date"])
        except (ValueError, KeyError, TypeError):
            continue
        delta_h = (t - now).total_seconds() / 3600
        if delta_h < -12:  # 半日以上前のものは出さない
            continue
        out.append({
            "title": e.get("title", ""),
            "country": e.get("country", ""),
            "impact": impact,
            "time": int(t.timestamp()),
            "in_window": (impact == "High") and (-hours_back <= delta_h <= hours_ahead),
        })
    out.sort(key=lambda x: x["time"])
    return out[:30]
