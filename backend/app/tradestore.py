"""実発注トレードの記録 (SQLite)。

予測履歴(predictions)や記録専用ポジション(positions)とは別テーブル。
ここに入るのは「ブローカーに実在する / 実在した」建玉だけ。

アプリを再起動しても決済エンジンが続きを引き継げるよう、
トレール状態(建値ストップ済みか・建玉後の最高安値)も永続化する。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "predictions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  broker TEXT NOT NULL,
  env TEXT NOT NULL,
  trade_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  side TEXT NOT NULL,
  units REAL NOT NULL,
  entry_price REAL NOT NULL,
  entry_time TEXT NOT NULL,
  initial_sl REAL,
  tp REAL,
  current_sl REAL,
  atr REAL,
  entry_score INTEGER,
  prob_up REAL,
  risk_ccy REAL,
  be_done INTEGER NOT NULL DEFAULT 0,
  extreme REAL,
  status TEXT NOT NULL DEFAULT 'open',
  close_price REAL,
  close_time TEXT,
  pnl_pips REAL,
  pnl_ccy REAL,
  close_reason TEXT,
  UNIQUE(broker, env, trade_id)
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, env);
"""

COLS = ("id", "broker", "env", "trade_id", "symbol", "interval", "side", "units",
        "entry_price", "entry_time", "initial_sl", "tp", "current_sl", "atr",
        "entry_score", "prob_up", "risk_ccy", "be_done", "extreme", "status",
        "close_price", "close_time", "pnl_pips", "pnl_ccy", "close_reason")

_SEL = ",".join(COLS)


def _conn() -> sqlite3.Connection:
    """決済ループ・予測ループ・APIが同じDBを触るのでWAL+待機を設定する。"""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(_SCHEMA)
    return c


def _row(r) -> dict:
    return dict(zip(COLS, r))


def record_open(**kw) -> int:
    fields = ("broker", "env", "trade_id", "symbol", "interval", "side", "units",
              "entry_price", "entry_time", "initial_sl", "tp", "current_sl",
              "atr", "entry_score", "prob_up", "risk_ccy", "extreme")
    vals = [kw.get(f) for f in fields]
    with _conn() as c:
        cur = c.execute(
            f"INSERT INTO trades({','.join(fields)}) "
            f"VALUES ({','.join('?' * len(fields))})", vals)
        return cur.lastrowid


def open_trades(env: str | None = None) -> list[dict]:
    q = f"SELECT {_SEL} FROM trades WHERE status='open'"
    args: tuple = ()
    if env:
        q += " AND env=?"
        args = (env,)
    with _conn() as c:
        return [_row(r) for r in c.execute(q + " ORDER BY id", args).fetchall()]


def get(row_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute(f"SELECT {_SEL} FROM trades WHERE id=?", (row_id,)).fetchone()
    return _row(r) if r else None


def find(broker: str, env: str, trade_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute(f"SELECT {_SEL} FROM trades WHERE broker=? AND env=? "
                      "AND trade_id=?", (broker, env, trade_id)).fetchone()
    return _row(r) if r else None


def update_trail(row_id: int, current_sl: float | None = None,
                 extreme: float | None = None, be_done: bool | None = None,
                 atr: float | None = None) -> None:
    sets, args = [], []
    for col, val in (("current_sl", current_sl), ("extreme", extreme),
                     ("be_done", None if be_done is None else int(be_done)),
                     ("atr", atr)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    if not sets:
        return
    args.append(row_id)
    with _conn() as c:
        c.execute(f"UPDATE trades SET {','.join(sets)} WHERE id=?", args)


def mark_closed(row_id: int, close_price: float, close_time: str,
                pnl_pips: float, pnl_ccy: float, reason: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE trades SET status='closed', close_price=?, close_time=?, "
            "pnl_pips=?, pnl_ccy=?, close_reason=? WHERE id=?",
            (close_price, close_time, pnl_pips, pnl_ccy, reason, row_id))


def history(env: str, limit: int = 50, symbol: str | None = None) -> list[dict]:
    q = f"SELECT {_SEL} FROM trades WHERE status='closed' AND env=?"
    args: list = [env]
    if symbol:
        q += " AND symbol=?"
        args.append(symbol)
    q += " ORDER BY close_time DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def count_open(env: str) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM trades WHERE status='open' AND env=?",
                         (env,)).fetchone()[0]


def has_open(env: str, symbol: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM trades WHERE status='open' AND env=? "
                         "AND symbol=?", (env, symbol)).fetchone() is not None


def realized_since(env: str, since_iso: str) -> tuple[float, int]:
    """指定時刻以降に決済した損益(口座通貨)と件数。日次損失上限の判定に使う。"""
    with _conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(pnl_ccy),0), COUNT(*) FROM trades "
            "WHERE status='closed' AND env=? AND close_time >= ?",
            (env, since_iso)).fetchone()
    return float(r[0]), int(r[1])


def today_start_utc() -> str:
    """日次リセットの基準。FXの日替わりに合わせて NY 17:00 = UTC 22:00 とする。"""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start.isoformat()


def stats(env: str) -> dict:
    """決済済みトレードの通算成績。"""
    with _conn() as c:
        r = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_ccy),0), "
            "COALESCE(SUM(CASE WHEN pnl_ccy > 0 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN pnl_ccy > 0 THEN pnl_ccy ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN pnl_ccy < 0 THEN -pnl_ccy ELSE 0 END),0), "
            "COALESCE(SUM(pnl_pips),0) "
            "FROM trades WHERE status='closed' AND env=?", (env,)).fetchone()
    n, total, wins, gross_win, gross_loss, pips = r
    return {
        "count": n,
        "pnl_ccy": round(total, 0),
        "pnl_pips": round(pips, 1),
        "win_rate": round(wins / n * 100, 1) if n else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
    }
