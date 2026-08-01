"""半自動売買エンジンのシナリオテスト。

実行:  cd backend && source .venv/Scripts/activate && python tests/test_trading.py

外部APIには接続しない。価格は手で流し込み、一時DBを使うので
本物の予測履歴やペーパー口座には一切触れない。
検証内容: ロット計算、SL/TPの向き、建値ストップ、トレーリング(ATR/固定pips)、
          SLが不利方向に動かないこと、発注ガード、残高と実現損益の整合。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["FX_SYNTHETIC"] = "1"

import pandas as pd

from app import config, gmo, store, trading, tradestore
from app.broker import paper
from app.broker.paper import PaperBroker

TMP = Path(tempfile.mkdtemp()) / "test.db"
for mod in (store, tradestore, paper):
    mod.DB = TMP

PASS, FAIL = [], []


def check(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) and isinstance(got, (int, float)) else got == want
    (PASS if ok else FAIL).append(f"{name}: got={got} want={want}")
    print(("  OK  " if ok else "  NG  ") + f"{name}: got={got} want={want}")


# --- 価格とATRを手で制御する -------------------------------------------------
PRICE = {"bid": 150.000, "ask": 150.003}
ATR = 0.100
BARS = pd.DataFrame(
    {"open": [150.0] * 200, "high": [150.05] * 200,
     "low": [149.95] * 200, "close": [150.0] * 200},
    index=pd.date_range("2026-01-01", periods=200, freq="15min", tz="UTC"))


def fake_ticker(symbol=None):
    return [{"symbol": symbol or "USD_JPY", "bid": f"{PRICE['bid']:.3f}",
             "ask": f"{PRICE['ask']:.3f}", "status": "OPEN"}]


gmo.get_ticker = fake_ticker
trading.bars = lambda s, i: BARS
trading.atr_of = lambda s, i: ATR

CFG = {
    "trading_enabled": True, "broker": "paper", "kill_switch": False,
    "risk_pct": 1.0, "max_units": 100_000, "max_positions": 3,
    "one_per_symbol": True, "daily_loss_limit_pct": 3.0,
    "manage_interval_sec": 5, "sl_atr_mult": 1.5, "breakeven_atr": 1.0,
    "breakeven_offset_pips": 1.0, "trail_mode": "atr", "trail_atr_mult": 1.5,
    "trail_start_pips": 15.0, "trail_fixed_pips": 10.0, "trail_swing_bars": 10,
    "exit_on_flip": False, "close_before_news": False,
}
config.load = lambda: dict(CFG)

broker = PaperBroker()
trading.get_broker = lambda name=None: broker
broker.reset(1_000_000.0)


def set_price(bid):
    PRICE["bid"] = round(bid, 3)
    PRICE["ask"] = round(bid + 0.003, 3)


print("\n[1] ロット計算 (リスク1% / 有効証拠金100万円 / 損切り15pips)")
sz = trading.size_for("USD_JPY", 150.000, 149.850, 1_000_000, 1.0, 100_000, "JPY")
check("許容損失", sz["risk_amount"], 10_000)
check("数量 (1000単位に切り捨て)", sz["units"], 66_000)   # 10000/0.150 = 66,666
check("実際のリスク額", sz["risk_ccy"], 9_900)            # 66,000 x 0.150
check("損切り幅", sz["sl_pips"], 15.0)

sz2 = trading.size_for("USD_JPY", 150.000, 149.990, 1_000_000, 1.0, 100_000, "JPY")
check("上限で丸められる", sz2["units"], 100_000)
check("capped フラグ", sz2["capped"], True)

sz3 = trading.size_for("USD_JPY", 150.000, 145.000, 100_000, 0.5, 100_000, "JPY")
check("小さすぎる場合の too_small", sz3["too_small"], True)


print("\n[1b] 自動算出されるSL/TPの向き")
set_price(150.000)
pl = trading.plan("USD_JPY", "15m", "LONG")
check("買い: SL < 現在値", pl["sl"] < pl["price"], True)
check("買い: TP > 現在値", pl["tp"] > pl["price"], True)
check("買い: TPは0.5ATR以上離れている", pl["tp"] - pl["price"] >= 0.05 - 1e-9, True)
ps = trading.plan("USD_JPY", "15m", "SHORT")
check("売り: SL > 現在値", ps["sl"] > ps["price"], True)
check("売り: TP < 現在値", ps["tp"] < ps["price"], True)


print("\n[2] 買いポジション: 建値ストップ → ATRトレーリング → SL到達で決済")
set_price(150.000)
res = trading.place("USD_JPY", "15m", "LONG", tp=151.000)
tid = res["id"]
check("建値", res["entry_price"], 150.003)       # ask で約定
check("初期SL (建値 - 1.5ATR)", res["sl"], 150.003 - 0.150, 1e-6)

# 含み損の間はトレーリングを作動させない
set_price(149.950)
trading.manage_once()
row = tradestore.get(tid)
check("含み損の間はSL据え置き", row["current_sl"], res["sl"], 1e-6)

# 含み益が出たらATR追従が働く (最高値 - 1.5ATR)
set_price(150.050)
trading.manage_once()
row = tradestore.get(tid)
check("含み益が出たらATR追従で切り上げ", row["current_sl"], 149.900, 1e-6)
check("建値ストップ未移行", bool(row["be_done"]), False)

# 含み益が 1 ATR に到達 → 建値ストップへ
set_price(150.110)   # bid 150.110, 建値150.003 から +0.107 >= ATR 0.100
trading.manage_once()
row = tradestore.get(tid)
check("建値ストップへ移行", bool(row["be_done"]), True)
check("SL = 建値 + 1pip", row["current_sl"], 150.013, 1e-6)

# さらに伸びる → ATRトレーリングが建値ストップを追い越す
set_price(150.400)
trading.manage_once()
row = tradestore.get(tid)
check("SL = 最高値 - 1.5ATR", row["current_sl"], 150.250, 1e-6)

# 押し戻されてもSLは下がらない
set_price(150.300)
trading.manage_once()
row = tradestore.get(tid)
check("押し戻されてもSLは下がらない", row["current_sl"], 150.250, 1e-6)

# SL到達 → 決済
set_price(150.240)
trading.manage_once()
row = tradestore.get(tid)
check("SL到達で決済", row["status"], "closed")
check("決済理由", row["close_reason"], "トレーリングストップ発動")
check("決済値", row["close_price"], 150.250, 1e-6)
check("獲得pips", row["pnl_pips"], round((150.250 - 150.003) / 0.01, 1), 0.05)


print("\n[3] 売りポジション: SLは切り下がるだけ / 利確到達")
set_price(150.000)
CFG["breakeven_atr"] = 1.0
res2 = trading.place("USD_JPY", "15m", "SHORT", tp=149.000)
tid2 = res2["id"]
check("建値 (bid で約定)", res2["entry_price"], 150.000)
check("初期SL (建値 + 1.5ATR)", res2["sl"], 150.150, 1e-6)

set_price(149.880)   # ask 149.883 → 含み益 0.117 >= ATR
trading.manage_once()
row = tradestore.get(tid2)
check("売りの建値ストップ = 建値 - 1pip", row["current_sl"], 149.990, 1e-6)

set_price(149.600)
trading.manage_once()
row = tradestore.get(tid2)
check("売りのトレーリング = 最安値 + 1.5ATR", row["current_sl"], 149.603 + 0.150, 1e-6)

set_price(149.700)
trading.manage_once()
row = tradestore.get(tid2)
check("戻してもSLは上がらない", row["current_sl"], 149.753, 1e-6)

set_price(149.760)   # ask 149.763 >= SL 149.753 → 損切り側で決済
trading.manage_once()
row = tradestore.get(tid2)
check("売りもSL到達で決済", row["status"], "closed")
check("売りの獲得pipsは正", row["pnl_pips"] > 0, True)


print("\n[4] 利確ライン到達")
set_price(150.000)
CFG["breakeven_atr"] = 0          # 建値ストップを切ってTPまで走らせる
CFG["trail_mode"] = "off"
res3 = trading.place("USD_JPY", "15m", "LONG", sl=149.800, tp=150.200)
tid3 = res3["id"]
set_price(150.250)
trading.manage_once()
row = tradestore.get(tid3)
check("TP到達で決済", row["status"], "closed")
check("決済理由", row["close_reason"], "利確ライン到達")
check("決済値はTP", row["close_price"], 150.200, 1e-6)
CFG["breakeven_atr"] = 1.0
CFG["trail_mode"] = "atr"


print("\n[5] 発注ガード")
set_price(150.000)
try:
    trading.place("USD_JPY", "15m", "LONG", sl=150.500, tp=150.900)
    check("SLが逆向きなら拒否", "通ってしまった", "拒否される")
except ValueError as e:
    check("SLが逆向きなら拒否", "拒否", "拒否")

try:
    trading.place("USD_JPY", "15m", "LONG", units=500)
    check("最小単位未満は拒否", "通ってしまった", "拒否される")
except ValueError:
    check("最小単位未満は拒否", "拒否", "拒否")

CFG["trading_enabled"] = False
try:
    trading.place("USD_JPY", "15m", "LONG")
    check("発注機能オフなら拒否", "通ってしまった", "拒否される")
except PermissionError:
    check("発注機能オフなら拒否", "拒否", "拒否")
CFG["trading_enabled"] = True

CFG["kill_switch"] = True
try:
    trading.place("USD_JPY", "15m", "LONG")
    check("キルスイッチで拒否", "通ってしまった", "拒否される")
except PermissionError:
    check("キルスイッチで拒否", "拒否", "拒否")
CFG["kill_switch"] = False

r = trading.place("USD_JPY", "15m", "LONG")
try:
    trading.place("USD_JPY", "15m", "LONG")
    check("同一ペアの重複を拒否", "通ってしまった", "拒否される")
except PermissionError:
    check("同一ペアの重複を拒否", "拒否", "拒否")
trading.close_trade(r["id"], "テスト後始末")

CFG["max_positions"] = 0
try:
    trading.place("USD_JPY", "15m", "LONG")
    check("同時保有上限で拒否", "通ってしまった", "拒否される")
except PermissionError:
    check("同時保有上限で拒否", "拒否", "拒否")
CFG["max_positions"] = 3


print("\n[6] 口座残高が決済損益と整合するか")
acc = broker.account()
hist = tradestore.history("paper", 100)
total = round(sum(h["pnl_ccy"] for h in hist), 0)
check("残高 = 初期100万 + 実現損益", round(acc.balance), round(1_000_000 + total))
st = tradestore.stats("paper")
print(f"      通算: {st['count']}件 / {st['pnl_ccy']:,.0f}円 / 勝率 {st['win_rate']}% / PF {st['profit_factor']}")


print("\n[7] 固定pipsトレール")
CFG["trail_mode"] = "fixed"
CFG["breakeven_atr"] = 0
CFG["trail_start_pips"] = 15.0
CFG["trail_fixed_pips"] = 10.0
set_price(150.000)
r7 = trading.place("USD_JPY", "15m", "LONG", tp=151.000)
set_price(150.100)   # +9.7pips → まだ開始前
trading.manage_once()
check("開始前はSL据え置き", tradestore.get(r7["id"])["current_sl"], r7["sl"], 1e-6)
set_price(150.200)   # +19.7pips → 開始
trading.manage_once()
check("SL = 最高値 - 10pips", tradestore.get(r7["id"])["current_sl"], 150.100, 1e-6)
trading.close_trade(r7["id"], "テスト後始末")

print("\n[8] 当日の損失上限")
CFG["trail_mode"] = "atr"
import sqlite3
from datetime import datetime, timezone
loss_id = tradestore.record_open(
    broker="paper", env="paper", trade_id="dummy-loss", symbol="EUR_JPY",
    interval="15m", side="LONG", units=10_000, entry_price=160.0,
    entry_time=datetime.now(timezone.utc).isoformat(), initial_sl=159.0,
    tp=161.0, current_sl=159.0, atr=0.1, entry_score=None, prob_up=None,
    risk_ccy=10_000, extreme=160.0)
equity = broker.account().equity
today_pnl, _ = tradestore.realized_since("paper", tradestore.today_start_utc())
# 上限は「当日の通算損益」に対する判定なので、既存の勝ち分も打ち消す必要がある
big_loss = -(equity * CFG["daily_loss_limit_pct"] / 100) - today_pnl - 1
tradestore.mark_closed(loss_id, 159.0, datetime.now(timezone.utc).isoformat(),
                       -100.0, big_loss, "テスト用の負けトレード")
blocks = trading.guards(CFG, broker, "USD_JPY")
check("損失上限に達したら発注を止める",
      any("当日の損失" in b for b in blocks), True)
try:
    trading.place("USD_JPY", "15m", "LONG")
    check("損失上限で place が拒否される", "通ってしまった", "拒否される")
except PermissionError:
    check("損失上限で place が拒否される", "拒否", "拒否")

# 上限を無効化すると再び発注できる
CFG["daily_loss_limit_pct"] = 0
check("上限0なら止まらない",
      any("当日の損失" in b for b in trading.guards(CFG, broker, "USD_JPY")), False)


print("\n" + "=" * 60)
print(f"合格 {len(PASS)} / 失敗 {len(FAIL)}")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
