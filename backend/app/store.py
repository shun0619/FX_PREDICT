"""予測履歴の保存 (SQLite)。次足確定後に正解判定を更新する。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "predictions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  bar_time TEXT NOT NULL,
  prob_up REAL NOT NULL,
  direction TEXT NOT NULL,
  close REAL NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  actual TEXT,
  correct INTEGER,
  UNIQUE(symbol, interval, bar_time)
);
"""


def _conn() -> sqlite3.Connection:
    """複数のループが同時に書くのでWAL+待機を設定する。"""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute(_SCHEMA)
    try:  # 既存DBへのマイグレーション
        c.execute("ALTER TABLE predictions ADD COLUMN score INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        # is_live=1: その足の時点で実際に出した予測(成績の集計対象)
        # is_live=0: 停止中の空白を後から埋めたもの。学習済み期間を再計算した
        #            イン・サンプル値なので、成績には絶対に混ぜない
        c.execute("ALTER TABLE predictions ADD COLUMN is_live INTEGER DEFAULT 1")
        # 既存データ: スコアが付いているものだけが本物のリアルタイム予測
        c.execute("UPDATE predictions SET is_live = (score IS NOT NULL)")
    except sqlite3.OperationalError:
        pass
    return c


def save_prediction(symbol: str, interval: str, bar_time: str,
                    prob_up: float, direction: str, close: float,
                    score: int | None = None, is_live: bool = True) -> None:
    """予測を記録する。

    is_live=False は「停止中の空白を後から埋めた予測」。学習済み期間を
    再計算した値なので実力より高く出る。成績集計からは除外される。
    """
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO predictions"
            "(symbol, interval, bar_time, prob_up, direction, close, score, is_live)"
            "VALUES (?,?,?,?,?,?,?,?)",
            (symbol, interval, bar_time, prob_up, direction, close, score,
             int(is_live)))
        if score is not None:
            # 先にスコア無しで保存されていた場合は補完する
            c.execute(
                "UPDATE predictions SET score=? "
                "WHERE symbol=? AND interval=? AND bar_time=? AND score IS NULL",
                (score, symbol, interval, bar_time))


def resolve(symbol: str, interval: str, bar_time: str, next_close: float) -> None:
    """次足確定後に正誤を記録。"""
    with _conn() as c:
        row = c.execute(
            "SELECT close, direction FROM predictions "
            "WHERE symbol=? AND interval=? AND bar_time=? AND actual IS NULL",
            (symbol, interval, bar_time)).fetchone()
        if not row:
            return
        close, direction = row
        actual = "UP" if next_close > close else "DOWN"
        c.execute(
            "UPDATE predictions SET actual=?, correct=? "
            "WHERE symbol=? AND interval=? AND bar_time=?",
            (actual, int(actual == direction), symbol, interval, bar_time))


def accuracy_for(symbol: str, interval: str, direction: str,
                 prob_lo: float, prob_hi: float) -> tuple[float | None, int]:
    """似た確率帯・同方向の過去予測の実測的中率。(accuracy, 件数)"""
    with _conn() as c:
        r = c.execute(
            "SELECT AVG(correct), COUNT(*) FROM predictions "
            "WHERE symbol=? AND interval=? AND direction=? "
            "AND prob_up BETWEEN ? AND ? AND correct IS NOT NULL AND is_live=1",
            (symbol, interval, direction, prob_lo, prob_hi)).fetchone()
    return (r[0], r[1]) if r else (None, 0)


def known_bar_times(symbol: str, interval: str, since: str) -> set[str]:
    """既に記録済みの足の時刻。埋め戻しの要否判定に使う。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT bar_time FROM predictions WHERE symbol=? AND interval=? "
            "AND bar_time >= ?", (symbol, interval, since)).fetchall()
    return {r[0] for r in rows}


_BAR_SEC = {"15m": 900, "1h": 3600, "4h": 14400}


def recent_directions(symbol: str, interval: str, k: int = 2) -> list[str]:
    """直近k本の予測方向(新しい順)。エグジット判定の反転検知に使う。

    古い予測で決済してしまわないよう、k本分の期間 x 3 より古いものは返さない
    (常時予測がオフ・停止中などで履歴が止まっているケースを弾く)。
    """
    from datetime import datetime, timedelta, timezone
    span = _BAR_SEC.get(interval, 900) * max(k, 1) * 3
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=span)).isoformat(sep=" ")
    with _conn() as c:
        rows = c.execute(
            "SELECT direction FROM predictions WHERE symbol=? AND interval=? "
            "AND bar_time >= ? ORDER BY bar_time DESC LIMIT ?",
            (symbol, interval, cutoff, k)).fetchall()
    return [r[0] for r in rows]


def score_stats(symbol: str, interval: str) -> dict:
    """Entry Scoreの答え合わせ: スコア帯別の実測的中率。"""
    buckets = [("good", "65点以上 (良い)", 65, 101),
               ("fair", "45〜64点 (普通)", 45, 65),
               ("poor", "44点以下 (悪い)", 0, 45)]
    out = []
    with _conn() as c:
        for key, label, lo, hi in buckets:
            r = c.execute(
                "SELECT AVG(correct), COUNT(*) FROM predictions "
                "WHERE symbol=? AND interval=? AND correct IS NOT NULL AND is_live=1 "
                "AND score IS NOT NULL AND score >= ? AND score < ?",
                (symbol, interval, lo, hi)).fetchone()
            out.append({"key": key, "label": label,
                        "n": r[1] if r else 0,
                        "accuracy": round(r[0] * 100, 1) if r and r[0] is not None else None})
        unscored = c.execute(
            "SELECT COUNT(*) FROM predictions WHERE symbol=? AND interval=? "
            "AND correct IS NOT NULL AND is_live=1 AND score IS NULL",
            (symbol, interval)).fetchone()[0]
    return {"buckets": out, "unscored_resolved": unscored}


def history(symbol: str, interval: str, limit: int = 100) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT bar_time, prob_up, direction, close, actual, correct, score, is_live "
            "FROM predictions WHERE symbol=? AND interval=? "
            "ORDER BY bar_time DESC LIMIT ?",
            (symbol, interval, limit)).fetchall()
        # 成績は「その時点で実際に出した予測」のみ。埋め戻し分は学習済み期間の
        # 再計算(イン・サンプル)なので混ぜると実力より高く出る
        acc = c.execute(
            "SELECT AVG(correct), COUNT(*) FROM predictions "
            "WHERE symbol=? AND interval=? AND correct IS NOT NULL AND is_live=1",
            (symbol, interval)).fetchone()
        backfilled = c.execute(
            "SELECT COUNT(*) FROM predictions WHERE symbol=? AND interval=? "
            "AND correct IS NOT NULL AND is_live=0", (symbol, interval)).fetchone()[0]
    items = [{"bar_time": r[0], "prob_up": r[1], "direction": r[2],
              "close": r[3], "actual": r[4], "correct": r[5], "score": r[6],
              "is_live": bool(r[7])}
             for r in rows]
    return {
        "items": items,
        "resolved_accuracy": round(acc[0] * 100, 1) if acc and acc[0] is not None else None,
        "resolved_count": acc[1] if acc else 0,
        "backfilled_excluded": backfilled,
    }
