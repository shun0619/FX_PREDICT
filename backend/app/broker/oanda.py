"""OANDA v20 REST クライアント (デモ practice / 本番 live)。

環境変数:
  OANDA_TOKEN       個人アクセストークン (fxTrade の「APIアクセス管理」で発行)
  OANDA_ACCOUNT_ID  口座ID (例 101-009-1234567-001)
  OANDA_ENV         practice (既定) / live

practice と live はホスト名とトークンが違うだけで仕様は同一。
本番移行は OANDA_ENV=live とトークン・口座IDの差し替えだけで済む。

SL/TP は建玉時に stopLossOnFill / takeProfitOnFill でサーバー側に置く。
このアプリが落ちても損切りは OANDA 側に残る — これがペーパー口座との決定的な違い。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from .base import Account, Broker, BrokerError, BrokerTrade, CloseResult

HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def _digits(symbol: str) -> int:
    return 3 if symbol.endswith("_JPY") else 5


def _fmt(symbol: str, price: float) -> str:
    return f"{price:.{_digits(symbol)}f}"


def configured() -> bool:
    return bool(os.environ.get("OANDA_TOKEN") and os.environ.get("OANDA_ACCOUNT_ID"))


class OandaBroker(Broker):
    name = "oanda"
    supports_server_stops = True

    def __init__(self, env: str | None = None, token: str | None = None,
                 account_id: str | None = None):
        self.env = (env or os.environ.get("OANDA_ENV", "practice")).lower()
        if self.env not in HOSTS:
            raise BrokerError(f"OANDA_ENV は practice か live: {self.env}")
        self.token = token or os.environ.get("OANDA_TOKEN", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")
        if not self.token or not self.account_id:
            raise BrokerError(
                "OANDA_TOKEN と OANDA_ACCOUNT_ID が未設定です")
        self.base = f"{HOSTS[self.env]}/v3/accounts/{self.account_id}"
        self._client = httpx.Client(
            timeout=20,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "Accept-Datetime-Format": "RFC3339"})

    # --- 低レベル ---
    def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = self._client.request(method, self.base + path, **kw)
        except httpx.HTTPError as e:
            raise BrokerError(f"OANDA接続失敗: {e}") from e
        if r.status_code >= 400:
            try:
                msg = r.json().get("errorMessage") or r.text
            except Exception:
                msg = r.text
            raise BrokerError(f"OANDA {r.status_code}: {msg}")
        return r.json()

    # --- 参照 ---
    def account(self) -> Account:
        a = self._req("GET", "/summary")["account"]
        return Account(
            broker=self.name, env=self.env, currency=a.get("currency", "JPY"),
            balance=float(a["balance"]),
            equity=float(a.get("NAV", a["balance"])),
            margin_used=float(a.get("marginUsed", 0)),
            margin_available=float(a.get("marginAvailable", 0)),
            unrealized_pl=float(a.get("unrealizedPL", 0)),
            is_live=self.is_live)

    def price(self, symbol: str) -> tuple[float, float]:
        d = self._req("GET", "/pricing", params={"instruments": symbol})
        prices = d.get("prices") or []
        if not prices:
            raise BrokerError(f"{symbol} の価格が取得できません")
        p = prices[0]
        if p.get("tradeable") is False:
            raise BrokerError(f"{symbol} は現在取引できません(休場)")
        return float(p["bids"][0]["price"]), float(p["asks"][0]["price"])

    @staticmethod
    def _to_trade(t: dict) -> BrokerTrade:
        units = float(t.get("currentUnits") or t.get("initialUnits") or 0)
        sl = t.get("stopLossOrder", {}).get("price")
        tp = t.get("takeProfitOrder", {}).get("price")
        return BrokerTrade(
            trade_id=str(t["id"]), symbol=t["instrument"],
            side="LONG" if units > 0 else "SHORT", units=abs(units),
            entry_price=float(t["price"]), open_time=t["openTime"],
            sl=float(sl) if sl else None, tp=float(tp) if tp else None,
            price=0.0, unrealized_pl=float(t.get("unrealizedPL", 0)))

    def trades(self) -> list[BrokerTrade]:
        d = self._req("GET", "/openTrades")
        out = [self._to_trade(t) for t in d.get("trades", [])]
        if out:
            try:  # 現在値はまとめて1リクエストで取る
                names = ",".join(sorted({t.symbol for t in out}))
                pr = self._req("GET", "/pricing", params={"instruments": names})
                book = {p["instrument"]: p for p in pr.get("prices", [])}
                for t in out:
                    p = book.get(t.symbol)
                    if p:
                        t.price = float(p["bids"][0]["price"] if t.side == "LONG"
                                        else p["asks"][0]["price"])
            except BrokerError:
                pass
        return out

    def trade(self, trade_id: str) -> BrokerTrade | None:
        d = self._req("GET", f"/trades/{trade_id}")["trade"]
        return self._to_trade(d) if d.get("state") == "OPEN" else None

    def closed_info(self, trade_id: str) -> CloseResult | None:
        """SL/TPがOANDA側で発動して決済された建玉の約定情報を拾う。"""
        try:
            t = self._req("GET", f"/trades/{trade_id}")["trade"]
        except BrokerError:
            return None
        if t.get("state") == "OPEN":
            return None
        return CloseResult(
            trade_id=str(t["id"]),
            close_price=float(t.get("averageClosePrice") or t["price"]),
            pnl_ccy=float(t.get("realizedPL", 0)),
            close_time=t.get("closeTime") or datetime.now(timezone.utc).isoformat())

    # --- 執行 ---
    def open(self, symbol: str, side: str, units: float,
             sl: float | None = None, tp: float | None = None) -> BrokerTrade:
        n = int(abs(units))
        if n <= 0:
            raise BrokerError("数量が0以下です")
        order: dict = {
            "type": "MARKET", "instrument": symbol,
            "units": str(n if side == "LONG" else -n),
            "timeInForce": "FOK", "positionFill": "DEFAULT",
        }
        # SL/TPはサーバー側に置く。アプリが落ちても損切りは残る
        if sl is not None:
            order["stopLossOnFill"] = {"price": _fmt(symbol, sl), "timeInForce": "GTC"}
        if tp is not None:
            order["takeProfitOnFill"] = {"price": _fmt(symbol, tp), "timeInForce": "GTC"}

        d = self._req("POST", "/orders", json={"order": order})
        fill = d.get("orderFillTransaction")
        if not fill or not fill.get("tradeOpened"):
            reject = d.get("orderRejectTransaction") or d.get("orderCancelTransaction") or {}
            raise BrokerError(
                f"発注が約定しませんでした: {reject.get('reason') or d.get('errorMessage') or d}")
        opened = fill["tradeOpened"]
        return BrokerTrade(
            trade_id=str(opened["tradeID"]), symbol=symbol, side=side,
            units=n, entry_price=float(opened.get("price") or fill["price"]),
            open_time=fill["time"], sl=sl, tp=tp,
            price=float(fill["price"]), unrealized_pl=0.0)

    def modify(self, trade_id: str, sl: float | None = None,
               tp: float | None = None) -> None:
        body: dict = {}
        symbol = None
        if sl is not None or tp is not None:
            t = self.trade(trade_id)
            if t is None:
                raise BrokerError("該当する建玉がありません")
            symbol = t.symbol
        if sl is not None:
            body["stopLoss"] = {"price": _fmt(symbol, sl), "timeInForce": "GTC"}
        if tp is not None:
            body["takeProfit"] = {"price": _fmt(symbol, tp), "timeInForce": "GTC"}
        if not body:
            return
        self._req("PUT", f"/trades/{trade_id}/orders", json=body)

    def close(self, trade_id: str) -> CloseResult:
        d = self._req("PUT", f"/trades/{trade_id}/close", json={"units": "ALL"})
        fill = d.get("orderFillTransaction")
        if not fill:
            raise BrokerError(f"決済に失敗しました: {d.get('errorMessage') or d}")
        closed = (fill.get("tradesClosed") or [{}])[0]
        return CloseResult(
            trade_id=str(trade_id), close_price=float(fill["price"]),
            pnl_ccy=float(closed.get("realizedPL") or fill.get("pl") or 0),
            close_time=fill["time"])
