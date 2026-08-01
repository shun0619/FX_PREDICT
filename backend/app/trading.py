"""半自動売買エンジン: 手動エントリー → 自動決済。

役割分担
  人   : 入るかどうか、どちらに入るかを決めてボタンを押す
  機械 : ロット計算、損切り・利確の設置、トレーリング、決済の執行

自動決済の順序(LONGの例。SHORTは符号が逆)
  1. 建玉時に 初期SL = 建値 - ATR×sl_atr_mult、TP = entry.py の算出値 を置く
  2. 含み益が ATR×breakeven_atr に達したら SL を建値+αへ (建値ストップ)
  3. 以降 SL = max(現SL, 建玉後の最高値 - ATR×trail_atr_mult) で追従
     ※SLは決して不利な方向へ動かさない
  4. SL/TP到達で決済

ブローカーが SL/TP をサーバー側に置ける場合 (OANDA) は、
SLの更新をAPIへ反映するだけで、発動判定はブローカーに任せる。
アプリやPCが落ちても損切りは生き残る。
ペーパー口座はサーバー側の注文が存在しないため、この監視ループが発動判定を行う。
つまりアプリが止まっている間は損切りも止まる — ペーパーはあくまで練習用。
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from threading import Lock

import pandas as pd

from . import config, econcal, entry as entrylogic, gmo, model, store, tradestore
from .broker import Broker, BrokerError, get_broker
from .broker.paper import PaperBroker, quote_to_jpy
from .indicators import add_indicators

MIN_UNITS = 1_000
UNIT_STEP = 1_000
SL_EPS_PIPS = 0.2   # SLをブローカーへ送り直す最小変化幅

_BAR_DAYS = {"15m": 20, "1h": 60, "4h": 200}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits(symbol: str) -> int:
    return 3 if symbol.endswith("_JPY") else 5


# --- 足データの短期キャッシュ (監視ループが毎秒APIを叩かないため) ---
_bars_cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
_BARS_TTL = 30.0


def bars(symbol: str, interval: str) -> pd.DataFrame:
    key = (symbol, interval)
    hit = _bars_cache.get(key)
    if hit and time.time() - hit[0] < _BARS_TTL:
        return hit[1]
    iv = gmo.INTERVAL_MAP[interval]
    df = gmo.drop_incomplete(
        gmo.fetch_klines(symbol, iv, _BAR_DAYS.get(interval, 20)), iv)
    _bars_cache[key] = (time.time(), df)
    return df


def atr_of(symbol: str, interval: str) -> float:
    df = bars(symbol, interval)
    v = float(add_indicators(df)["atr14"].iloc[-1])
    return v if v == v and v > 0 else float(df["close"].iloc[-1]) * 1e-4


# --- ロット計算 -------------------------------------------------------------

def ccy_factor(symbol: str, account_ccy: str) -> float:
    """クオート通貨1単位 = 口座通貨いくらか。"""
    quote = symbol.split("_")[1]
    if quote == account_ccy:
        return 1.0
    if account_ccy == "JPY":
        return quote_to_jpy(symbol)
    return 1.0  # 口座通貨がJPY以外かつクオートと異なる場合は換算せず等価とみなす


def size_for(symbol: str, entry_price: float, sl: float, equity: float,
             risk_pct: float, max_units: int, account_ccy: str = "JPY") -> dict:
    """1トレードの損失が equity × risk_pct% に収まる数量を返す。"""
    dist = abs(entry_price - sl)
    if dist <= 0:
        raise ValueError("損切り幅が0です")
    factor = ccy_factor(symbol, account_ccy)
    risk_amount = equity * risk_pct / 100.0
    raw = risk_amount / (dist * factor)
    units = int(raw // UNIT_STEP * UNIT_STEP)
    capped = units > max_units
    units = min(max(units, 0), int(max_units))
    return {
        "units": units,
        "risk_amount": round(risk_amount, 0),
        "risk_ccy": round(units * dist * factor, 0),
        "sl_pips": round(dist / gmo.pip_size(symbol), 1),
        "capped": capped,
        "too_small": units < MIN_UNITS,
    }


# --- 発注前のガード ---------------------------------------------------------

def guards(cfg: dict, broker: Broker, symbol: str | None = None) -> list[str]:
    """新規発注を止めるべき理由の一覧。空なら発注可。"""
    blocks: list[str] = []
    if not cfg.get("trading_enabled"):
        blocks.append("発注機能がオフです(設定 → 売買執行 で有効化)")
    if cfg.get("kill_switch"):
        blocks.append("キルスイッチが入っています")

    env = broker.env
    if tradestore.count_open(env) >= int(cfg.get("max_positions", 3)):
        blocks.append(f"同時保有の上限({cfg.get('max_positions')}件)に達しています")
    if symbol and cfg.get("one_per_symbol", True) and tradestore.has_open(env, symbol):
        blocks.append(f"{symbol} は既に建玉があります")

    limit_pct = float(cfg.get("daily_loss_limit_pct", 0) or 0)
    if limit_pct > 0:
        pnl, _ = tradestore.realized_since(env, tradestore.today_start_utc())
        try:
            equity = float(broker.account().equity)
        except Exception as e:
            # 口座残高が読めない状態で発注を通すと日次損失上限が無効化される。
            # 安全側に倒して発注を止める (フェイルクローズ)
            blocks.append(f"口座情報が取得できず損失上限を確認できません: {e}")
            equity = 0.0
        if equity > 0 and pnl <= -equity * limit_pct / 100:
            blocks.append(
                f"当日の損失が上限({limit_pct}%)に達しました。明日まで新規発注を停止します")
    return blocks


# --- 発注プラン (画面に出す前の見積り) --------------------------------------

def plan(symbol: str, interval: str, side: str) -> dict:
    """指定方向で今エントリーした場合の想定。発注はしない。"""
    cfg = config.load()
    broker = get_broker()
    df = bars(symbol, interval)
    if len(df) < 100:
        raise ValueError("確定足が不足しています")

    bid, ask = broker.price(symbol)
    entry_price = ask if side == "LONG" else bid

    conf = 0.0
    try:
        iv = gmo.INTERVAL_MAP[interval]
        extras = model.load_extras(symbol, iv, _BAR_DAYS.get(interval, 20))
        conf = float(model.predict(df, symbol, iv, extras)["confidence"])
    except Exception:
        pass

    atr = atr_of(symbol, interval)
    sl_mult = float(cfg.get("sl_atr_mult", 1.5))
    long = side == "LONG"
    sign = 1 if long else -1

    # entry.py のTPは「直近確定足の終値」基準。実際の約定価格に合わせて
    # 距離だけを引き継ぐ(終値と現在値が離れているとTPが手前に来てしまうため)
    _, tp_ref = entrylogic.sl_tp_for(df, long, conf, symbol)
    last_close = float(df["close"].iloc[-1])
    tp_dist = abs(tp_ref - last_close)
    sl_dist = sl_mult * atr
    # 損切り幅を下回るTPは損益バランスが1.0未満になるので底上げする。
    # スプレッド往復ぶんも最低限確保する
    tp_dist = max(tp_dist, sl_dist, (ask - bid) * 3)
    tp = entry_price + sign * tp_dist
    sl = entry_price - sign * sl_dist
    d = _digits(symbol)
    sl, tp = round(sl, d), round(tp, d)

    try:
        acc = broker.account()
        equity, ccy = acc.equity, acc.currency
    except Exception:
        equity, ccy = 0.0, "JPY"
    sizing = size_for(symbol, entry_price, sl, equity,
                      float(cfg.get("risk_pct", 1.0)),
                      int(cfg.get("max_units", 100_000)), ccy)

    pip = gmo.pip_size(symbol)
    return {
        "symbol": symbol, "interval": interval, "side": side,
        "price": round(entry_price, d), "bid": bid, "ask": ask,
        "spread_pips": round((ask - bid) / pip, 1),
        "sl": sl, "tp": tp,
        "sl_pips": round(abs(entry_price - sl) / pip, 1),
        "tp_pips": round(abs(tp - entry_price) / pip, 1),
        "rr": round(abs(tp - entry_price) / abs(entry_price - sl), 2)
              if entry_price != sl else 0.0,
        "atr": round(atr, d),
        "currency": ccy, "equity": equity,
        **sizing,
        "blocks": guards(cfg, broker, symbol),
        "server_stops": broker.supports_server_stops,
        "is_live": broker.is_live,
    }


# --- 発注 -------------------------------------------------------------------

def place(symbol: str, interval: str, side: str, units: int | None = None,
          sl: float | None = None, tp: float | None = None) -> dict:
    if side not in ("LONG", "SHORT"):
        raise ValueError("side は LONG か SHORT")
    cfg = config.load()
    broker = get_broker()
    blocked = guards(cfg, broker, symbol)
    if blocked:
        raise PermissionError(" / ".join(blocked))

    p = plan(symbol, interval, side)
    sl = float(sl) if sl is not None else p["sl"]
    tp = float(tp) if tp is not None else p["tp"]
    units = int(units) if units is not None else int(p["units"])

    # NaN / inf を先に弾く (比較をすり抜けて異常な注文になるのを防ぐ)
    for name, val in (("損切り", sl), ("利確", tp)):
        if not math.isfinite(val):
            raise ValueError(f"{name}の値が不正です")
    if not math.isfinite(p["price"]) or p["price"] <= 0:
        raise ValueError("価格が取得できていません")

    # 現在値から極端に離れたSL/TPは入力ミスとみなす (ATRの50倍 or 20%)
    far = max(50 * p["atr"], p["price"] * 0.2)
    if abs(p["price"] - sl) > far or abs(tp - p["price"]) > far:
        raise ValueError("損切り・利確が現在値から離れすぎています。値を確認してください")

    if units < MIN_UNITS:
        raise ValueError(
            f"数量が最小単位({MIN_UNITS:,})未満です。リスク%を上げるか損切り幅を狭めてください")
    if units > int(cfg.get("max_units", 100_000)):
        raise ValueError(f"数量が上限({int(cfg.get('max_units')):,})を超えています")
    # SLの向きが逆だと損切りが即発動する。ここで必ず止める
    if side == "LONG" and not (sl < p["price"] < tp):
        raise ValueError("買いは SL < 現在値 < TP の関係になっている必要があります")
    if side == "SHORT" and not (tp < p["price"] < sl):
        raise ValueError("売りは TP < 現在値 < SL の関係になっている必要があります")

    # 見積り時の価格からの「距離」を保持する。約定価格は発注時に取り直されるため、
    # 価格をそのまま渡すとズレた分だけSL/TPが近く(遠く)なり、
    # 最悪すでに逆側にあって即損切り・注文拒否になる
    sign = 1 if side == "LONG" else -1
    sl_dist = abs(p["price"] - sl)
    tp_dist = abs(tp - p["price"])

    t = broker.open(symbol, side, units, sl=sl, tp=tp)

    # 実際の約定価格に合わせてSL/TPを引き直す (ズレが1pip以上あるときだけ)
    d = _digits(symbol)
    pip = gmo.pip_size(symbol)
    if abs(t.entry_price - p["price"]) >= pip:
        sl = round(t.entry_price - sign * sl_dist, d)
        tp = round(t.entry_price + sign * tp_dist, d)
        try:
            broker.modify(t.trade_id, sl=sl, tp=tp)
        except BrokerError:
            sl = t.sl if t.sl is not None else sl
            tp = t.tp if t.tp is not None else tp

    # 発注時点のAI予測を記録しておく(後で「スコアが高い時ほど勝てたか」を検証するため)
    score = prob = None
    try:
        item = store.history(symbol, interval, 1)["items"][0]
        score, prob = item.get("score"), item.get("prob_up")
    except Exception:
        pass
    row_id = tradestore.record_open(
        broker=broker.name, env=broker.env, trade_id=t.trade_id, symbol=symbol,
        interval=interval, side=side, units=units, entry_price=t.entry_price,
        entry_time=t.open_time, initial_sl=sl, tp=tp, current_sl=sl,
        atr=p["atr"], entry_score=score, prob_up=prob,
        risk_ccy=p.get("risk_ccy"), extreme=t.entry_price)
    return {"id": row_id, "trade_id": t.trade_id, "symbol": symbol, "side": side,
            "units": units, "entry_price": t.entry_price, "sl": sl, "tp": tp,
            "env": broker.env, "server_stops": broker.supports_server_stops}


# --- 自動決済エンジン -------------------------------------------------------

def _desired_sl(row: dict, cfg: dict, price: float, atr: float,
                extreme: float) -> tuple[float, bool, str]:
    """現在の状況から置くべきSLを返す。(sl, 建値ストップ済みか, 説明)"""
    long = row["side"] == "LONG"
    sign = 1 if long else -1
    entry = row["entry_price"]
    pip = gmo.pip_size(row["symbol"])
    cur = row["current_sl"] if row["current_sl"] is not None else row["initial_sl"]
    be_done = bool(row["be_done"])
    note = "初期の損切り位置"

    cands = [cur]

    # 建値ストップ (浮動小数点の丸め誤差で閾値ちょうどが漏れないよう許容誤差を持たせる)
    be_atr = float(cfg.get("breakeven_atr", 0) or 0)
    if be_atr > 0 and (extreme - entry) * sign >= be_atr * atr - pip * 0.01:
        offset = float(cfg.get("breakeven_offset_pips", 0)) * pip
        cands.append(entry + sign * offset)
        if not be_done:
            note = "建値ストップへ移行"
        be_done = True

    # トレーリング (含み益が出てから作動させる。建値割れの状態でSLを
    # 勝手に切り上げると、初期の損切り幅より早く狩られてしまうため)
    in_profit = (extreme - entry) * sign > 0
    if cfg.get("trail_mode", "atr") == "atr" and in_profit:
        cands.append(extreme - sign * float(cfg.get("trail_atr_mult", 1.5)) * atr)

    # SLは有利な方向にしか動かさない
    new_sl = max(cands) if long else min(cands)
    if abs(new_sl - cur) > pip * 0.05 and note == "初期の損切り位置":
        note = "トレーリングで切り上げ" if long else "トレーリングで切り下げ"
    return round(new_sl, _digits(row["symbol"])), be_done, note


def _pnl_pips(row: dict, price: float) -> float:
    sign = 1 if row["side"] == "LONG" else -1
    return round((price - row["entry_price"]) * sign / gmo.pip_size(row["symbol"]), 1)


def _finalize(row: dict, close_price: float, pnl_ccy: float,
              close_time: str, reason: str) -> None:
    tradestore.mark_closed(row["id"], round(close_price, _digits(row["symbol"])),
                           close_time, _pnl_pips(row, close_price),
                           round(pnl_ccy, 0), reason)


# 手動決済と自動決済エンジンが同じ建玉を同時に閉じないようにする
_close_lock = Lock()


def close_trade(row_id: int, reason: str = "手動決済") -> dict:
    with _close_lock:
        row = tradestore.get(row_id)
        if row is None or row["status"] != "open":
            raise ValueError("決済できる建玉がありません")
        broker = get_broker(row["broker"])
        res = broker.close(row["trade_id"])
        _finalize(row, res.close_price, res.pnl_ccy, res.close_time, reason)
    return {"id": row_id, "close_price": res.close_price,
            "pnl_ccy": res.pnl_ccy, "pnl_pips": _pnl_pips(row, res.close_price),
            "reason": reason}


def close_all(reason: str = "一括決済") -> list[dict]:
    out = []
    for row in tradestore.open_trades():
        try:
            out.append(close_trade(row["id"], reason))
        except Exception as e:
            out.append({"id": row["id"], "error": str(e)})
    return out


def _reconcile(row: dict, broker: Broker) -> bool:
    """ブローカー側で既に決済済みなら記録を閉じる。閉じたら True。"""
    info = broker.closed_info(row["trade_id"])
    if info is None:
        return False
    sign = 1 if row["side"] == "LONG" else -1
    cur_sl = row["current_sl"] or row["initial_sl"] or 0
    if row["tp"] and (info.close_price - row["tp"]) * sign >= -gmo.pip_size(row["symbol"]):
        reason = "利確ライン到達 (ブローカー側TP)"
    elif (info.close_price - cur_sl) * sign <= gmo.pip_size(row["symbol"]):
        reason = ("トレーリングストップ発動" if bool(row["be_done"])
                  else "損切りライン到達 (ブローカー側SL)")
    else:
        reason = "ブローカー側で決済"
    _finalize(row, info.close_price, info.pnl_ccy, info.close_time, reason)
    return True


_price_fail: dict[int, int] = {}


def adopt_orphans() -> list[dict]:
    """ブローカー側にあるのに記録が無い建玉を取り込む。

    発注した直後にアプリが落ちる / DB書き込みに失敗するなどで、
    「ブローカーには玉があるがこちらは知らない」状態が起こりうる。
    そのままだと決済エンジンの監視対象外になり、損切りが効かない。
    """
    events: list[dict] = []
    try:
        broker = get_broker()
        live = broker.trades()
    except Exception:
        return events
    if not live:
        return events

    known = {r["trade_id"] for r in tradestore.open_trades()}
    for t in live:
        if t.trade_id in known:
            continue
        if tradestore.find(broker.name, broker.env, t.trade_id):
            continue   # 決済済みとして記録済み
        # SL/TPがブローカー側にあればそれを、無ければATRから引き直す
        try:
            atr = atr_of(t.symbol, "1h")
        except Exception:
            atr = t.entry_price * 1e-4
        sign = 1 if t.side == "LONG" else -1
        sl = t.sl if t.sl is not None else round(
            t.entry_price - sign * 1.5 * atr, _digits(t.symbol))
        tp = t.tp
        rid = tradestore.record_open(
            broker=broker.name, env=broker.env, trade_id=t.trade_id,
            symbol=t.symbol, interval="1h", side=t.side, units=t.units,
            entry_price=t.entry_price, entry_time=t.open_time or _now(),
            initial_sl=sl, tp=tp, current_sl=sl, atr=atr, extreme=t.entry_price)
        if broker.supports_server_stops and t.sl is None:
            try:
                broker.modify(t.trade_id, sl=sl)
            except BrokerError:
                pass
        events.append({"id": rid, "error":
                       f"記録に無い建玉を取り込みました ({t.symbol} {t.side} "
                       f"{t.units:,.0f}通貨 @{t.entry_price})。損切り {sl} を設定して監視を開始します"})
    return events


def manage_once() -> list[dict]:
    """開いている全建玉を1巡見る。決済エンジンの本体。"""
    cfg = config.load()
    events: list[dict] = []
    rows = tradestore.open_trades()
    if not rows:
        return events

    brokers: dict[str, Broker] = {}
    live_ids: dict[str, set[str]] = {}
    for row in rows:
        try:
            b = brokers.get(row["broker"])
            if b is None:
                b = brokers[row["broker"]] = get_broker(row["broker"])
                live_ids[row["broker"]] = {t.trade_id for t in b.trades()}
        except Exception as e:
            events.append({"id": row["id"], "error": f"ブローカー接続失敗: {e}"})
            continue

        # ブローカー側に建玉が無い = 既にSL/TPで決済されている
        if row["trade_id"] not in live_ids[row["broker"]]:
            try:
                if _reconcile(row, b):
                    events.append({"id": row["id"], "closed": True,
                                   "reason": "ブローカー側で決済済み"})
                else:
                    # 建玉も約定履歴も見つからない(ペーパー口座のリセット等)。
                    # 記録だけ残り続けると上限判定が狂うので閉じる
                    tradestore.mark_closed(
                        row["id"], row["entry_price"], _now(), 0.0, 0.0,
                        "ブローカー側に建玉が見つからず記録を閉じました")
                    events.append({"id": row["id"], "closed": True,
                                   "reason": "ブローカー側に建玉なし"})
            except Exception as e:
                events.append({"id": row["id"], "error": str(e)})
            continue

        try:
            bid, ask = b.price(row["symbol"])
            _price_fail.pop(row["id"], None)
        except Exception as e:
            # 休場・一時的な失敗は次の巡回で見る。ただしサーバー側SLが無い
            # ブローカーで連続して取れない場合は損切りが効かないので警告する
            n = _price_fail.get(row["id"], 0) + 1
            _price_fail[row["id"]] = n
            if not b.supports_server_stops and n in (12, 60, 300):
                events.append({"id": row["id"],
                               "error": f"価格を{n}回連続で取得できません。"
                                        f"損切りの監視が止まっています ({e})"})
            continue
        long = row["side"] == "LONG"
        price = bid if long else ask  # 決済側の価格で判定する

        atr = row["atr"] or 0.0
        try:
            atr = atr_of(row["symbol"], row["interval"])
        except Exception:
            pass

        extreme = row["extreme"] if row["extreme"] is not None else row["entry_price"]
        extreme = max(extreme, price) if long else min(extreme, price)

        new_sl, be_done, note = _desired_sl(row, cfg, price, atr, extreme)
        cur_sl = row["current_sl"] if row["current_sl"] is not None else row["initial_sl"]
        moved = abs(new_sl - (cur_sl or new_sl)) >= gmo.pip_size(row["symbol"]) * SL_EPS_PIPS

        if b.supports_server_stops and moved:
            try:
                b.modify(row["trade_id"], sl=new_sl)
                events.append({"id": row["id"], "sl": new_sl, "note": note})
            except BrokerError as e:
                events.append({"id": row["id"], "error": f"SL更新失敗: {e}"})
                new_sl = cur_sl or new_sl
        elif moved:
            events.append({"id": row["id"], "sl": new_sl, "note": note})

        tradestore.update_trail(row["id"], current_sl=new_sl, extreme=extreme,
                                be_done=be_done, atr=atr)
        row = {**row, "current_sl": new_sl, "extreme": extreme, "be_done": be_done}

        # サーバー側に注文が無いブローカー(ペーパー)はここで発動判定する
        if not b.supports_server_stops:
            hit_sl = price <= new_sl if long else price >= new_sl
            hit_tp = row["tp"] is not None and (price >= row["tp"] if long
                                                else price <= row["tp"])
            if hit_sl or hit_tp:
                reason = ("利確ライン到達" if hit_tp else
                          "トレーリングストップ発動" if be_done else "損切りライン到達")
                # ペーパーでも「不利な方の価格」で約定させる。
                # SLは飛び越えた分だけ実際の約定が悪くなるので現在値を使い、
                # TPは指値なのでライン価格どまり(有利にすり抜けさせない)
                if hit_sl:
                    fill = price
                else:
                    fill = min(price, row["tp"]) if long else max(price, row["tp"])
                try:
                    with _close_lock:
                        # 手動決済と競合していないか直前に再確認する
                        cur = tradestore.get(row["id"])
                        if cur is None or cur["status"] != "open":
                            continue
                        res = (b.close(row["trade_id"], price=fill)
                               if isinstance(b, PaperBroker) else b.close(row["trade_id"]))
                        _finalize(row, res.close_price, res.pnl_ccy, res.close_time, reason)
                    events.append({"id": row["id"], "closed": True, "reason": reason})
                except Exception as e:
                    events.append({"id": row["id"], "error": f"決済失敗: {e}"})
                continue

        # 任意ルール: AI予測が2本連続で逆方向
        if cfg.get("exit_on_flip"):
            against = "DOWN" if long else "UP"
            dirs = store.recent_directions(row["symbol"], row["interval"], 2)
            if len(dirs) >= 2 and all(d == against for d in dirs[:2]):
                try:
                    events.append({**close_trade(row["id"], "AI予測が2本連続で逆方向"),
                                   "closed": True})
                except Exception as e:
                    events.append({"id": row["id"], "error": str(e)})
                continue

        # 任意ルール: 重要指標の直前は畳む
        if cfg.get("close_before_news"):
            try:
                if any(e["in_window"] for e in econcal.events_for(row["symbol"])):
                    events.append({**close_trade(row["id"], "重要指標の発表が近いため決済"),
                                   "closed": True})
                    continue
            except Exception:
                pass

    return events


# --- 自動エントリー -----------------------------------------------------------
#
# 実口座では絶対に自動発注しない。検証データが十分に貯まるまでの安全装置で、
# 外すときはこの定数を変える (コードを触る = 意識的な操作を強制する)。
AUTO_ENTRY_PAPER_ONLY = True

# 同じ足で何度も発注しないための記録 {(symbol, interval): bar_time}
_auto_seen: dict[tuple[str, str], str] = {}


def auto_entry_once() -> list[dict]:
    """設定条件を満たしたら自動で発注する。1巡分の判定結果を返す。"""
    cfg = config.load()
    if not cfg.get("auto_entry"):
        return []
    events: list[dict] = []

    try:
        broker = get_broker()
    except BrokerError as e:
        return [{"error": f"ブローカー接続失敗: {e}"}]
    if AUTO_ENTRY_PAPER_ONLY and broker.env != "paper":
        return [{"error": "自動エントリーはペーパー口座でのみ動作します"
                          "(実口座での自動発注はコード側でブロック)"}]

    tf = cfg.get("auto_interval", "1h")
    if tf not in gmo.INTERVAL_MAP:
        return [{"error": f"時間足の設定が不正です: {tf}"}]
    symbols = [s.strip() for s in str(cfg.get("auto_symbols", "")).split(",")
               if s.strip() in gmo.SYMBOLS]
    if not symbols:
        return [{"error": "対象ペアが設定されていません"}]

    min_prob = float(cfg.get("auto_min_prob", 0.65))
    min_score = int(cfg.get("auto_min_score", 65))
    min_rr = float(cfg.get("auto_min_rr", 1.0))
    iv = gmo.INTERVAL_MAP[tf]

    for symbol in symbols:
        try:
            df = bars(symbol, tf)
            if len(df) < 100:
                continue
            bar = str(df.index[-1])
            # 同じ足では1回しか判定しない
            if _auto_seen.get((symbol, tf)) == bar:
                continue

            blocked = guards(cfg, broker, symbol)
            if blocked:
                events.append({"symbol": symbol, "skip": " / ".join(blocked)})
                _auto_seen[(symbol, tf)] = bar
                continue

            if cfg.get("auto_skip_news", True):
                try:
                    if any(e["in_window"] for e in econcal.events_for(symbol)):
                        events.append({"symbol": symbol, "skip": "重要指標の警戒中"})
                        _auto_seen[(symbol, tf)] = bar
                        continue
                except Exception:
                    pass

            extras = model.load_extras(symbol, iv, _BAR_DAYS.get(tf, 20))
            pred = model.predict(df, symbol, iv, extras)
            acc, n = store.accuracy_for(symbol, tf, pred["direction"],
                                        pred["prob_up"] - 0.05, pred["prob_up"] + 0.05)
            plan_ = entrylogic.entry_plan(df, pred, symbol, acc, n)

            prob = pred["prob_up"]
            side = "LONG" if prob >= 0.5 else "SHORT"
            conf_prob = prob if side == "LONG" else 1 - prob
            _auto_seen[(symbol, tf)] = bar

            if conf_prob < min_prob:
                events.append({"symbol": symbol,
                               "skip": f"確率不足 {conf_prob:.0%} < {min_prob:.0%}"})
                continue
            if plan_["score"] < min_score:
                events.append({"symbol": symbol,
                               "skip": f"スコア不足 {plan_['score']} < {min_score}"})
                continue

            # 損益バランス(利確幅÷損切り幅)。1.0未満は「勝っても負けのほうが大きい」
            # 設定なので、勝率が5割前後だとトータルで負ける
            if min_rr > 0:
                p_ = plan(symbol, tf, side)
                if p_["rr"] < min_rr:
                    events.append({"symbol": symbol,
                                   "skip": f"損益バランス不足 1:{p_['rr']} < 1:{min_rr}"})
                    continue

            r = place(symbol, tf, side)
            events.append({"symbol": symbol, "opened": True, "side": side,
                           "prob": round(conf_prob, 3), "score": plan_["score"],
                           "units": r["units"], "entry": r["entry_price"],
                           "sl": r["sl"], "tp": r["tp"]})
        except (PermissionError, ValueError, BrokerError) as e:
            events.append({"symbol": symbol, "error": str(e)})
        except Exception as e:
            events.append({"symbol": symbol, "error": f"判定失敗: {e}"})
    return events


def open_view() -> list[dict]:
    """UI用: 記録とブローカーの現在値を突き合わせた建玉一覧。"""
    out = []
    brokers: dict[str, Broker] = {}
    for row in tradestore.open_trades():
        item = dict(row)
        try:
            b = brokers.get(row["broker"]) or brokers.setdefault(
                row["broker"], get_broker(row["broker"]))
            bid, ask = b.price(row["symbol"])
            price = bid if row["side"] == "LONG" else ask
            item["price"] = round(price, _digits(row["symbol"]))
            item["pnl_pips"] = _pnl_pips(row, price)
            item["pnl_ccy"] = round(
                (price - row["entry_price"]) * (1 if row["side"] == "LONG" else -1)
                * row["units"] * ccy_factor(row["symbol"], "JPY"), 0)
            sl = row["current_sl"] or row["initial_sl"]
            item["sl_distance_pips"] = round(
                abs(price - sl) / gmo.pip_size(row["symbol"]), 1) if sl else None
            item["trailing"] = bool(row["be_done"]) or (
                sl is not None and row["initial_sl"] is not None
                and abs(sl - row["initial_sl"]) > gmo.pip_size(row["symbol"]) * 0.1)
        except Exception as e:
            item["error"] = str(e)
        out.append(item)
    return out
