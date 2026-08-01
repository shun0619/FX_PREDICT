"""ペーパー口座(内部仮想約定)。

外部APIに接続せず、GMOコインの公開ティッカー(bid/ask)で約定させる。
スプレッドは実レートのbid/ask差がそのまま反映されるため、
「スプレッド分だけ不利に約定する」挙動は再現できる。

再現できないもの: スリッページ、約定拒否、週末ギャップ、スワップ金利。
本番前の最終確認は必ずデモ口座(OANDA practice)で行うこと。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import gmo
from .base import Account, Broker, BrokerError, BrokerTrade, CloseResult, entry_side_price

DB = Path(__file__).resolve().parent.parent.parent / "data" / "predictions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  currency TEXT NOT NULL DEFAULT 'JPY',
  balance REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  units REAL NOT NULL,
  entry_price REAL NOT NULL,
  open_time TEXT NOT NULL,
  sl REAL, tp REAL,
  status TEXT NOT NULL DEFAULT 'open',
  close_price REAL, close_time TEXT, pnl_ccy REAL
);
"""

INITIAL_BALANCE = 1_000_000.0  # 円。デモ口座の一般的な初期額に合わせる
LEVERAGE = 25.0                # 国内個人の上限


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(_SCHEMA)
    c.execute("INSERT OR IGNORE INTO paper_account(id, currency, balance) "
              "VALUES (1, 'JPY', ?)", (INITIAL_BALANCE,))
    c.commit()
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_to_jpy(symbol: str) -> float:
    """クオート通貨1単位 = 何円か。USD_JPY なら1.0、EUR_USD ならUSDJPYレート。"""
    if symbol.endswith("_JPY"):
        return 1.0
    quote = symbol.split("_")[1]
    if quote == "JPY":
        return 1.0
    d = gmo.get_ticker(f"{quote}_JPY")
    if not d:
        raise BrokerError(f"{quote}_JPY のレートが取得できず円換算できません")
    return (float(d[0]["bid"]) + float(d[0]["ask"])) / 2


class PaperBroker(Broker):
    name = "paper"
    env = "paper"
    supports_server_stops = False  # SL/TPはこのアプリの決済エンジンが見張る

    # --- 参照 ---
    def price(self, symbol: str) -> tuple[float, float]:
        d = gmo.get_ticker(symbol)
        if not d:
            raise BrokerError(f"{symbol} の価格が取得できません")
        return float(d[0]["bid"]), float(d[0]["ask"])

    def account(self) -> Account:
        with _conn() as c:
            ccy, balance = c.execute(
                "SELECT currency, balance FROM paper_account WHERE id=1").fetchone()
        open_trades = self.trades()
        unrealized = sum(t.unrealized_pl for t in open_trades)
        margin = 0.0
        for t in open_trades:
            margin += t.units * t.entry_price * quote_to_jpy(t.symbol) / LEVERAGE
        equity = balance + unrealized
        return Account(
            broker=self.name, env=self.env, currency=ccy, balance=round(balance, 0),
            equity=round(equity, 0), margin_used=round(margin, 0),
            margin_available=round(equity - margin, 0),
            unrealized_pl=round(unrealized, 0), is_live=False)

    def _rows(self, where: str, args: tuple) -> list[tuple]:
        with _conn() as c:
            return c.execute(
                "SELECT id, symbol, side, units, entry_price, open_time, sl, tp, "
                "status, close_price, close_time, pnl_ccy FROM paper_trades "
                + where, args).fetchall()

    def trades(self) -> list[BrokerTrade]:
        out: list[BrokerTrade] = []
        for r in self._rows("WHERE status='open' ORDER BY id", ()):
            (tid, symbol, side, units, entry, otime, sl, tp, *_rest) = r
            try:
                bid, ask = self.price(symbol)
                price = entry_side_price(bid, ask, side, closing=True)
                pnl = self._pnl(symbol, side, units, entry, price)
            except Exception:
                price, pnl = entry, 0.0  # 休場中など。価格が取れなくても建玉は返す
            out.append(BrokerTrade(
                trade_id=str(tid), symbol=symbol, side=side, units=units,
                entry_price=entry, open_time=otime, sl=sl, tp=tp,
                price=round(price, 5), unrealized_pl=round(pnl, 0)))
        return out

    def closed_info(self, trade_id: str) -> CloseResult | None:
        r = self._rows("WHERE id=? AND status='closed'", (trade_id,))
        if not r:
            return None
        row = r[0]
        return CloseResult(trade_id=str(row[0]), close_price=row[9],
                           close_time=row[10], pnl_ccy=row[11])

    @staticmethod
    def _pnl(symbol: str, side: str, units: float,
             entry: float, price: float) -> float:
        sign = 1 if side == "LONG" else -1
        return (price - entry) * sign * units * quote_to_jpy(symbol)

    # --- 執行 ---
    def open(self, symbol: str, side: str, units: float,
             sl: float | None = None, tp: float | None = None) -> BrokerTrade:
        if units <= 0:
            raise BrokerError("数量が0以下です")
        bid, ask = self.price(symbol)
        price = entry_side_price(bid, ask, side, closing=False)
        acc = self.account()
        need = units * price * quote_to_jpy(symbol) / LEVERAGE
        if need > acc.margin_available:
            raise BrokerError(
                f"証拠金不足: 必要 {need:,.0f}円 / 余力 {acc.margin_available:,.0f}円")
        now = _now()
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO paper_trades(symbol,side,units,entry_price,open_time,sl,tp)"
                " VALUES (?,?,?,?,?,?,?)",
                (symbol, side, units, price, now, sl, tp))
            tid = cur.lastrowid
        return BrokerTrade(trade_id=str(tid), symbol=symbol, side=side, units=units,
                           entry_price=price, open_time=now, sl=sl, tp=tp,
                           price=price, unrealized_pl=0.0)

    def modify(self, trade_id: str, sl: float | None = None,
               tp: float | None = None) -> None:
        sets, args = [], []
        if sl is not None:
            sets.append("sl=?"); args.append(sl)
        if tp is not None:
            sets.append("tp=?"); args.append(tp)
        if not sets:
            return
        args.append(trade_id)
        with _conn() as c:
            c.execute(f"UPDATE paper_trades SET {','.join(sets)} "
                      "WHERE id=? AND status='open'", args)

    def close(self, trade_id: str, price: float | None = None) -> CloseResult:
        rows = self._rows("WHERE id=? AND status='open'", (trade_id,))
        if not rows:
            raise BrokerError("該当する建玉がありません")
        (tid, symbol, side, units, entry, *_rest) = rows[0]
        if price is None:
            bid, ask = self.price(symbol)
            price = entry_side_price(bid, ask, side, closing=True)
        pnl = self._pnl(symbol, side, units, entry, price)
        now = _now()
        with _conn() as c:
            c.execute("UPDATE paper_trades SET status='closed', close_price=?, "
                      "close_time=?, pnl_ccy=? WHERE id=?", (price, now, pnl, tid))
            c.execute("UPDATE paper_account SET balance = balance + ? WHERE id=1", (pnl,))
        return CloseResult(trade_id=str(tid), close_price=round(price, 5),
                           pnl_ccy=round(pnl, 0), close_time=now)

    # --- ペーパー口座固有 ---
    def reset(self, balance: float = INITIAL_BALANCE) -> None:
        """残高と建玉を初期化する(検証用)。"""
        with _conn() as c:
            c.execute("DELETE FROM paper_trades")
            c.execute("UPDATE paper_account SET balance=? WHERE id=1", (balance,))
