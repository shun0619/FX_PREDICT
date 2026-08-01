"""バックテスト: スプレッドコスト込みの次足方向シミュレーション。

戦略: prob_up >= threshold でロング、prob_up <= 1-threshold でショート。
      1本保有して次足の終値で決済(足ごとの再評価)。
コスト: エントリーごとに spread_pips * pip_size を控除。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .gmo import pip_size


def run(df: pd.DataFrame, probs: pd.Series, symbol: str,
        threshold: float = 0.55, spread_pips: float = 0.2,
        initial_capital: float = 1_000_000, lot: float = 10_000) -> dict:
    pip = pip_size(symbol)
    close = df["close"]
    next_ret = close.shift(-1) - close  # 価格差

    side = pd.Series(0, index=df.index)
    side[probs >= threshold] = 1
    side[probs <= 1 - threshold] = -1

    valid = next_ret.notna() & probs.notna()
    side = side[valid]
    ret = next_ret[valid]

    pnl_price = side * ret - (side != 0).astype(int) * spread_pips * pip
    pnl_jpy = pnl_price * lot  # JPYペア前提の簡易換算(非JPYペアは通貨単位)
    equity = initial_capital + pnl_jpy.cumsum()

    trades_mask = side != 0
    n_trades = int(trades_mask.sum())
    wins = int(((pnl_price > 0) & trades_mask).sum())
    win_rate = wins / n_trades if n_trades else 0.0

    gross_win = float(pnl_jpy[trades_mask & (pnl_jpy > 0)].sum())
    gross_loss = float(-pnl_jpy[trades_mask & (pnl_jpy < 0)].sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy_pips = float(pnl_price[trades_mask].mean() / pip) if n_trades else 0.0

    peak = equity.cummax()
    dd = (peak - equity) / peak
    max_dd = float(dd.max()) if len(dd) else 0.0

    monthly = (pnl_jpy.groupby(pnl_jpy.index.tz_localize(None).to_period("M")).sum()
               / initial_capital * 100).round(2)
    monthly_list = [{"month": str(k), "return_pct": float(v)}
                    for k, v in monthly.items()]

    # 損益曲線 (最大500点に間引き)
    step = max(1, len(equity) // 500)
    eq = equity.iloc[::step]
    equity_curve = [{"time": int(t.timestamp()), "equity": round(float(v), 0)}
                    for t, v in eq.items()]

    # 直近トレード一覧
    recent = []
    tr = pd.DataFrame({"side": side[trades_mask], "entry": close[valid][trades_mask],
                       "pnl_pips": (pnl_price[trades_mask] / pip).round(1),
                       "pnl_jpy": pnl_jpy[trades_mask].round(0)})
    for t, r in tr.tail(20).iloc[::-1].iterrows():
        recent.append({"time": str(t), "type": "LONG" if r["side"] == 1 else "SHORT",
                       "entry": float(r["entry"]), "pnl_pips": float(r["pnl_pips"]),
                       "pnl_jpy": float(r["pnl_jpy"])})

    return {
        "symbol": symbol, "threshold": threshold, "spread_pips": spread_pips,
        "bars": int(valid.sum()), "n_trades": n_trades,
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else None,
        "expectancy_pips": round(expectancy_pips, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "total_pnl_jpy": round(float(pnl_jpy[trades_mask].sum()), 0),
        "initial_capital": initial_capital, "lot": lot,
        "period": {"from": str(df.index[0]), "to": str(df.index[-1])},
        "equity_curve": equity_curve,
        "monthly": monthly_list,
        "recent_trades": recent,
        "best_month": max(monthly_list, key=lambda x: x["return_pct"])["return_pct"] if monthly_list else 0,
        "worst_month": min(monthly_list, key=lambda x: x["return_pct"])["return_pct"] if monthly_list else 0,
        "avg_month": round(float(np.mean([m["return_pct"] for m in monthly_list])), 2) if monthly_list else 0,
    }
