"""ブローカー抽象化層。

執行先(ペーパー口座 / OANDA デモ / OANDA 本番)をこのインターフェースで
差し替える。上位(trading.py, main.py)はブローカーの実装を知らない。

単位の約束:
  units      : 基軸通貨の数量(正の整数)。LONG/SHORTは side で表す
  価格        : クオート通貨建て(USD_JPY なら円)
  pnl_ccy    : 口座通貨建ての損益(このアプリでは円想定)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Account:
    broker: str          # paper / oanda
    env: str             # paper / practice / live
    currency: str        # JPY など
    balance: float       # 残高
    equity: float        # 有効証拠金(残高 + 評価損益)
    margin_used: float
    margin_available: float
    unrealized_pl: float
    is_live: bool = False


@dataclass
class BrokerTrade:
    """ブローカー側に実在する建玉。"""
    trade_id: str
    symbol: str
    side: str            # LONG / SHORT
    units: float
    entry_price: float
    open_time: str       # ISO8601 (UTC)
    sl: float | None = None
    tp: float | None = None
    price: float = 0.0           # 現在の決済側価格
    unrealized_pl: float = 0.0   # 口座通貨建て


@dataclass
class CloseResult:
    trade_id: str
    close_price: float
    pnl_ccy: float
    close_time: str


class BrokerError(RuntimeError):
    """発注・決済の失敗。呼び出し側はこれを捕捉してユーザーに返す。"""


class Broker:
    """全ブローカー共通のインターフェース。"""

    name: str = "base"
    env: str = "paper"
    #: True なら SL/TP をブローカー側(サーバー)に置ける。
    #: この場合アプリが落ちても損切りは生き残るため、必ず建玉時に送る。
    supports_server_stops: bool = False

    @property
    def is_live(self) -> bool:
        return self.env == "live"

    # --- 参照 ---
    def account(self) -> Account:
        raise NotImplementedError

    def price(self, symbol: str) -> tuple[float, float]:
        """(bid, ask) を返す。"""
        raise NotImplementedError

    def trades(self) -> list[BrokerTrade]:
        """未決済の建玉一覧。"""
        raise NotImplementedError

    def trade(self, trade_id: str) -> BrokerTrade | None:
        """個別の建玉。決済済み・存在しない場合は None。"""
        for t in self.trades():
            if t.trade_id == trade_id:
                return t
        return None

    def closed_info(self, trade_id: str) -> CloseResult | None:
        """ブローカー側で決済済みの建玉の約定情報。未対応なら None。"""
        return None

    # --- 執行 ---
    def open(self, symbol: str, side: str, units: float,
             sl: float | None = None, tp: float | None = None) -> BrokerTrade:
        raise NotImplementedError

    def modify(self, trade_id: str, sl: float | None = None,
               tp: float | None = None) -> None:
        raise NotImplementedError

    def close(self, trade_id: str) -> CloseResult:
        raise NotImplementedError


def entry_side_price(bid: float, ask: float, side: str, closing: bool) -> float:
    """約定価格。LONGは ask で買い bid で売り、SHORTはその逆。"""
    if side == "LONG":
        return bid if closing else ask
    return ask if closing else bid
