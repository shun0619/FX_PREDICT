"""ブローカーの選択。

設定 (settings.json) の broker が
  "paper"  -> PaperBroker  (外部接続なし・内部仮想約定)
  "oanda"  -> OandaBroker  (OANDA_ENV=practice でデモ、live で本番)

本番(live)は環境変数 FX_ALLOW_LIVE=1 が無いと選択できない。
うっかり本番口座に発注する事故を、コード側で1枚止める。
"""
from __future__ import annotations

import os
from threading import Lock

from .base import Account, Broker, BrokerError, BrokerTrade, CloseResult
from .oanda import OandaBroker, configured as oanda_configured
from .paper import PaperBroker

__all__ = ["Account", "Broker", "BrokerError", "BrokerTrade", "CloseResult",
           "PaperBroker", "OandaBroker", "get_broker", "reset_broker",
           "broker_status", "oanda_configured"]

_cache: dict[str, Broker] = {}
_lock = Lock()


def live_allowed() -> bool:
    return os.environ.get("FX_ALLOW_LIVE", "0") == "1"


def get_broker(name: str | None = None) -> Broker:
    from .. import config
    cfg = config.load()
    name = (name or cfg.get("broker", "paper")).lower()

    if name == "paper":
        key = "paper"
    elif name == "oanda":
        env = os.environ.get("OANDA_ENV", "practice").lower()
        if env == "live" and not live_allowed():
            raise BrokerError(
                "本番口座(live)は環境変数 FX_ALLOW_LIVE=1 が無いと使えません。"
                "デモで十分な検証を終えてから設定してください")
        key = f"oanda:{env}"
    else:
        raise BrokerError(f"未知のブローカー: {name}")

    with _lock:
        if key not in _cache:
            _cache[key] = PaperBroker() if name == "paper" else OandaBroker()
        return _cache[key]


def reset_broker() -> None:
    """設定変更後に接続を作り直す。"""
    with _lock:
        _cache.clear()


def broker_status() -> dict:
    """UI表示用。接続できているか、本番かどうかを返す。"""
    from .. import config
    cfg = config.load()
    name = cfg.get("broker", "paper")
    env = os.environ.get("OANDA_ENV", "practice").lower() if name == "oanda" else "paper"
    out = {
        "broker": name, "env": env,
        "is_live": name == "oanda" and env == "live",
        "live_allowed": live_allowed(),
        "oanda_configured": oanda_configured(),
        "connected": False, "error": None, "account": None,
        "server_stops": name == "oanda",
    }
    try:
        acc = get_broker().account()
        out["connected"] = True
        out["account"] = acc.__dict__
    except Exception as e:
        out["error"] = str(e)
    return out
