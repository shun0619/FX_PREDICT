"""FX Predict API (FastAPI)。

起動:  uvicorn app.main:app --reload --port 8000

分析(予測・バックテスト)に加えて、半自動売買の執行APIを持つ:
人が方向を決めてエントリー → 損切り・利確・トレーリングは自動。
執行先は設定の broker で切り替える (paper / oanda)。既定は発注機能オフ。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import backtest as bt
from . import broker as brokermod
from . import config, econcal, entry, gmo, model, store, trading, tradestore

INTERVALS = list(gmo.INTERVAL_MAP.keys())  # 1m 5m 15m 1h 4h
DEFAULT_DAYS = {"15m": 45, "1h": 120, "4h": 400}
TRAIN_DAYS = {"15m": 120, "1h": 365, "4h": 900}


def _interval(tf: str) -> str:
    if tf not in gmo.INTERVAL_MAP:
        raise HTTPException(400, f"interval must be one of {INTERVALS}")
    return gmo.INTERVAL_MAP[tf]


def _load(symbol: str, tf: str, days: int | None = None):
    if symbol not in gmo.SYMBOLS:
        raise HTTPException(400, f"symbol must be one of {gmo.SYMBOLS}")
    days = days or DEFAULT_DAYS[tf]
    try:
        return gmo.fetch_klines(symbol, _interval(tf), days)
    except Exception as e:
        raise HTTPException(502, f"データ取得失敗: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_resolver_loop()),
             asyncio.create_task(_auto_train_loop()),
             asyncio.create_task(_bg_predict_loop()),
             asyncio.create_task(_trade_manage_loop()),
             asyncio.create_task(_auto_entry_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="FX Predict API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def _resolver_loop():
    """未確定の予測を定期的に正解判定する(確定足のみ使用)。"""
    while True:
        await asyncio.sleep(60)
        try:
            for (symbol, tf) in list(_predicted_keys):
                interval = gmo.INTERVAL_MAP[tf]
                df = gmo.drop_incomplete(
                    gmo.fetch_klines(symbol, interval, DEFAULT_DAYS[tf]), interval)
                # bar i (確定) の予測を、bar i+1 (確定) の終値で採点
                # 窓を広めに取り、停止中に溜まった未採点分も拾う
                for i in range(max(0, len(df) - 500), len(df) - 1):
                    store.resolve(symbol, tf, str(df.index[i]),
                                  float(df["close"].iloc[i + 1]))
        except Exception:
            pass


_predicted_keys: set[tuple[str, str]] = set()

_REV_INTERVAL = {v: k for k, v in gmo.INTERVAL_MAP.items()}  # 15min -> 15m


def _retrain_all() -> str:
    """学習済みモデルを全て再学習(同期・別スレッドで実行)。"""
    results = []
    for f in sorted(model.MODEL_DIR.glob("*.txt")):
        symbol, interval = f.stem.rsplit("_", 1)  # USD_JPY_15min
        tf = _REV_INTERVAL.get(interval)
        if symbol not in gmo.SYMBOLS or tf is None:
            continue
        try:
            df = gmo.drop_incomplete(
                gmo.fetch_klines(symbol, interval, TRAIN_DAYS[tf]), interval)
            extras = model.load_extras(symbol, interval, TRAIN_DAYS[tf])
            meta = model.train(df, symbol, interval, extras=extras)
            results.append(f"{symbol} {tf}: WF精度 {meta.get('wf_mean_accuracy')}")
        except Exception as e:
            results.append(f"{symbol} {tf}: 失敗 ({e})")
    return " / ".join(results) if results else "学習済みモデルなし"


def _entry_score(df, pred, symbol: str, tf: str) -> int:
    """予測時点のEntry Scoreを算出(答え合わせ用に履歴へ併記する)。"""
    acc, n = store.accuracy_for(symbol, tf, pred["direction"],
                                pred["prob_up"] - 0.05, pred["prob_up"] + 0.05)
    return entry.entry_plan(df, pred, symbol, acc, n)["score"]


def _bg_predict_one(symbol: str, tf: str) -> None:
    interval = gmo.INTERVAL_MAP[tf]
    df = gmo.drop_incomplete(
        gmo.fetch_klines(symbol, interval, DEFAULT_DAYS[tf]), interval)
    if len(df) < 100:
        return
    extras = model.load_extras(symbol, interval, DEFAULT_DAYS[tf])

    # 最新足: エントリースコア付きで記録
    r = model.predict(df, symbol, interval, extras)
    score = _entry_score(df, r, symbol, tf)
    store.save_prediction(symbol, tf, r["bar_time"],
                          r["prob_up"], r["direction"], r["close"], score)

    # 空白期間の埋め戻し: PCスリープ等で記録できなかった確定足を遡って記録。
    # 履歴の見た目を埋めるだけの参考値で、成績集計には入れない (is_live=False)。
    # 毎分やる必要はないので、実際に穴が空いているときだけ実行する。
    recent = df.index[-200:]
    known = store.known_bar_times(symbol, tf, str(recent[0]))
    missing = [t for t in recent[:-1] if str(t) not in known]
    if missing:
        probs = model.predict_series(df, symbol, interval, extras)
        closes = df["close"]
        for t in missing:
            p = probs.get(t)
            if p is None or p != p:
                continue
            store.save_prediction(symbol, tf, str(t), round(float(p), 4),
                                  "UP" if p >= 0.5 else "DOWN", float(closes.loc[t]),
                                  is_live=False)
    _predicted_keys.add((symbol, tf))


async def _bg_predict_loop():
    """常時予測: ブラウザを開いていなくても、学習済み全モデルで
    足ごとに予測して履歴に記録する(実測正解率が貯まる)。"""
    await asyncio.sleep(15)
    while True:
        try:
            if config.load().get("background_predict", True):
                for f in list(model.MODEL_DIR.glob("*.txt")):
                    symbol, interval = f.stem.rsplit("_", 1)
                    tf = _REV_INTERVAL.get(interval)
                    if symbol not in gmo.SYMBOLS or tf is None:
                        continue
                    try:
                        await asyncio.to_thread(_bg_predict_one, symbol, tf)
                    except Exception:
                        pass  # 休場・未学習などはスキップ
        except Exception:
            pass
        await asyncio.sleep(60)


async def _auto_train_loop():
    """自動再学習: 有効時、指定間隔(日)ごとに全モデルを再学習する。"""
    await asyncio.sleep(30)  # 起動直後は避ける
    while True:
        try:
            cfg = config.load()
            if cfg["auto_train"]:
                last = cfg.get("last_auto_train")
                due = last is None or (
                    (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
                    >= int(cfg.get("auto_train_days", 7)))
                if due:
                    log = await asyncio.to_thread(_retrain_all)
                    config.save({
                        "last_auto_train": datetime.now(timezone.utc).isoformat(),
                        "auto_train_log": log,
                    })
        except Exception:
            pass
        await asyncio.sleep(1800)  # 30分ごとにチェック


_trade_log: list[dict] = []   # 決済エンジンの直近イベント(UI表示・調査用)
_manage_state = {"last_run": None, "last_error": None}


_LOG_KEYS = ("error", "opened", "skip", "closed", "sl")


def _log_events(events: list[dict]) -> None:
    """画面に表示できる情報を持つイベントだけ記録する。"""
    if not events:
        return
    now = datetime.now(timezone.utc).isoformat()
    for e in events:
        if any(e.get(k) is not None for k in _LOG_KEYS):
            _trade_log.append({**e, "at": now})
    del _trade_log[:-200]


async def _trade_manage_loop():
    """自動決済エンジン。建玉を監視してSLを動かし、到達したら決済する。

    発注機能がオフでも、既に建っている玉は守り続ける
    (途中でオフにしたときに損切りが外れると危ないため)。
    """
    await asyncio.sleep(10)
    # 起動時: 記録に無い建玉がブローカー側に残っていないか確認して取り込む
    try:
        _log_events(await asyncio.to_thread(trading.adopt_orphans))
    except Exception as e:
        _manage_state["last_error"] = f"起動時チェック失敗: {e}"

    n = 0
    while True:
        cfg = config.load()
        wait = max(2, int(cfg.get("manage_interval_sec", 5)))
        try:
            # 5分ごとに孤児チェック(発注直後のクラッシュなどに備える)
            n += 1
            if n % max(1, int(300 / wait)) == 0:
                _log_events(await asyncio.to_thread(trading.adopt_orphans))
            if tradestore.open_trades():
                events = await asyncio.to_thread(trading.manage_once)
                _log_events(events)
                _manage_state["last_error"] = next(
                    (e["error"] for e in events if e.get("error")), None)
            _manage_state["last_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _manage_state["last_error"] = str(e)
        await asyncio.sleep(wait)


async def _auto_entry_loop():
    """自動エントリー。足が確定するたびに条件を判定して発注する。"""
    await asyncio.sleep(20)
    while True:
        try:
            if config.load().get("auto_entry"):
                events = await asyncio.to_thread(trading.auto_entry_once)
                if events:
                    _log_events([{**e, "auto": True} for e in events])
                    opened = [e for e in events if e.get("opened")]
                    errs = [e for e in events if e.get("error")]
                    msg = (f"{len(opened)}件発注" if opened else
                           errs[0]["error"] if errs else
                           next((e["skip"] for e in events if e.get("skip")), "条件未達"))
                    config.save({"auto_last_run": datetime.now(timezone.utc).isoformat(),
                                 "auto_log": msg})
        except Exception as e:
            try:
                config.save({"auto_log": f"エラー: {e}"})
            except Exception:
                pass
        await asyncio.sleep(30)


@app.get("/api/health")
def health():
    return {"ok": True, "synthetic": gmo.SYNTHETIC}


@app.get("/api/symbols")
def symbols():
    return {"symbols": gmo.SYMBOLS, "intervals": INTERVALS}


@app.get("/api/ticker")
def ticker(symbol: str = Query("USD_JPY")):
    try:
        data = gmo.get_ticker(symbol)
    except Exception as e:
        raise HTTPException(502, f"ticker取得失敗: {e}")
    if not data:
        raise HTTPException(404, "symbol not found")
    d = data[0]
    bid, ask = float(d["bid"]), float(d["ask"])
    return {"symbol": symbol, "bid": bid, "ask": ask,
            "spread_pips": round((ask - bid) / gmo.pip_size(symbol), 1),
            "status": d.get("status"), "timestamp": d.get("timestamp")}


@app.get("/api/chart-data")
def chart_data(symbol: str = "USD_JPY", interval: str = "15m",
               days: int | None = None, limit: int = 500):
    from .indicators import add_indicators
    df = add_indicators(_load(symbol, interval, days)).tail(limit)

    def col(name, digits=5):
        return [None if v != v else round(float(v), digits)
                for v in df[name]]

    return {
        "symbol": symbol, "interval": interval,
        "time": [int(t.timestamp()) for t in df.index],
        "open": col("open"), "high": col("high"),
        "low": col("low"), "close": col("close"),
        "ema20": col("ema20"), "ema50": col("ema50"), "sma20": col("sma20"),
        "rsi14": col("rsi14", 2),
        "macd": col("macd", 6), "macd_signal": col("macd_signal", 6),
        "macd_hist": col("macd_hist", 6),
        "bb_upper": col("bb_upper"), "bb_lower": col("bb_lower"),
        "atr14": col("atr14", 6),
    }


@app.get("/api/predict")
def predict(symbol: str = "USD_JPY", interval: str = "15m"):
    # 確定足のみで予測(進行中の足は学習時と条件が異なるため使わない)
    df = gmo.drop_incomplete(_load(symbol, interval), gmo.INTERVAL_MAP[interval])
    if len(df) < 100:
        raise HTTPException(502, "確定足が不足しています")
    extras = model.load_extras(symbol, gmo.INTERVAL_MAP[interval],
                               DEFAULT_DAYS[interval])
    try:
        result = model.predict(df, symbol, gmo.INTERVAL_MAP[interval], extras)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    score = _entry_score(df, result, symbol, interval)
    store.save_prediction(symbol, interval, result["bar_time"],
                          result["prob_up"], result["direction"], result["close"],
                          score)
    _predicted_keys.add((symbol, interval))
    result["entry_score"] = score
    return result


@app.get("/api/entry-plan")
def entry_plan(symbol: str = "USD_JPY", interval: str = "15m"):
    """エントリー適正度スコアと損切り・利確目安。"""
    df = gmo.drop_incomplete(_load(symbol, interval), gmo.INTERVAL_MAP[interval])
    if len(df) < 100:
        raise HTTPException(502, "確定足が不足しています")
    extras = model.load_extras(symbol, gmo.INTERVAL_MAP[interval],
                               DEFAULT_DAYS[interval])
    try:
        pred = model.predict(df, symbol, gmo.INTERVAL_MAP[interval], extras)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    acc, n = store.accuracy_for(symbol, interval, pred["direction"],
                                pred["prob_up"] - 0.05, pred["prob_up"] + 0.05)
    plan = entry.entry_plan(df, pred, symbol, acc, n)
    # 答え合わせ用にスコア付きで履歴保存(既存行にはスコアのみ補完)
    store.save_prediction(symbol, interval, pred["bar_time"], pred["prob_up"],
                          pred["direction"], pred["close"], plan["score"])
    _predicted_keys.add((symbol, interval))
    return plan


@app.get("/api/score-stats")
def score_stats(symbol: str = "USD_JPY", interval: str = "15m"):
    """Entry Scoreの答え合わせ(スコア帯別の実測的中率)。"""
    return store.score_stats(symbol, interval)


@app.get("/api/history")
def history(symbol: str = "USD_JPY", interval: str = "15m", limit: int = 100):
    return store.history(symbol, interval, limit)


@app.get("/api/backtest")
def backtest(symbol: str = "USD_JPY", interval: str = "15m",
             days: int | None = None, threshold: float = 0.55,
             spread_pips: float = 0.2, lot: float = 10_000):
    d = days or TRAIN_DAYS[interval]
    iv = gmo.INTERVAL_MAP[interval]
    df = gmo.drop_incomplete(_load(symbol, interval, d), iv)

    # 学習時に記録したウォークフォワード予測(未学習データに対する予測)を使う。
    # 全データで学習したモデルで過去を予測すると答えを知っている問題を解くことに
    # なり、勝率・損益が実力より大幅に高く出てしまう
    probs = model.load_oos(symbol, iv)
    if probs is None:
        raise HTTPException(
            409, "検証用データがありません。「AI予測」画面でモデルを再学習してください "
                 "(再学習時にウォークフォワード予測を保存します)")
    probs = probs[probs.index.isin(df.index)]
    if len(probs) < 50:
        raise HTTPException(409, "検証区間が短すぎます。モデルを再学習してください")
    df = df[df.index >= probs.index[0]]
    result = bt.run(df, probs.reindex(df.index), symbol, threshold, spread_pips, lot=lot)
    result["out_of_sample"] = True
    return result


@app.post("/api/train")
def train(symbol: str = "USD_JPY", interval: str = "15m",
          days: int | None = None):
    d = days or TRAIN_DAYS[interval]
    df = gmo.drop_incomplete(_load(symbol, interval, d), gmo.INTERVAL_MAP[interval])
    extras = model.load_extras(symbol, gmo.INTERVAL_MAP[interval], d)
    try:
        meta = model.train(df, symbol, gmo.INTERVAL_MAP[interval], extras=extras)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return meta


# ---------------------------------------------------------------------------
# 半自動売買 (手動エントリー → 自動決済)
# ---------------------------------------------------------------------------

@app.get("/api/broker/status")
def broker_status():
    """接続先・口座残高・本番かデモかを返す。画面上部の表示に使う。"""
    st = brokermod.broker_status()
    st["manage"] = dict(_manage_state)
    st["open_count"] = tradestore.count_open(st["env"])
    return st


@app.post("/api/broker/reconnect")
def broker_reconnect():
    brokermod.reset_broker()
    return brokermod.broker_status()


@app.get("/api/trade/plan")
def trade_plan(symbol: str = "USD_JPY", interval: str = "15m",
               side: str = "LONG"):
    """今この方向でエントリーしたらどうなるかの見積り(発注はしない)。"""
    if side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side は LONG か SHORT")
    try:
        return trading.plan(symbol, interval, side)
    except brokermod.BrokerError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


class OrderBody(BaseModel):
    symbol: str = "USD_JPY"
    interval: str = "15m"
    side: str = "LONG"
    units: int | None = None
    sl: float | None = None
    tp: float | None = None
    confirm: bool = False   # 本番口座では明示確認を必須にする


@app.post("/api/trade/open")
def trade_open(body: OrderBody):
    try:
        if brokermod.get_broker().is_live and not body.confirm:
            raise HTTPException(428, "本番口座です。確認にチェックを入れてください")
        return trading.place(body.symbol, body.interval, body.side,
                             body.units, body.sl, body.tp)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except brokermod.BrokerError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/trade/open")
def trade_open_list():
    return {"trades": trading.open_view(),
            "log": _trade_log[-20:][::-1],
            "manage": dict(_manage_state)}


@app.post("/api/trade/close")
def trade_close(id: int = Query(..., description="trades テーブルの id")):
    try:
        return trading.close_trade(id, "手動決済")
    except brokermod.BrokerError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/trade/close-all")
def trade_close_all():
    """緊急停止。全建玉を成行で決済する。"""
    return {"results": trading.close_all("一括決済(緊急停止)")}


@app.get("/api/trade/history")
def trade_history(limit: int = 50, symbol: str | None = None):
    env = brokermod.broker_status()["env"]
    return {"env": env,
            "items": tradestore.history(env, limit, symbol),
            "stats": tradestore.stats(env)}


@app.post("/api/paper/reset")
def paper_reset(balance: float = 1_000_000.0):
    """ペーパー口座の残高と建玉を初期化する(検証用)。"""
    b = brokermod.get_broker("paper")
    if not isinstance(b, brokermod.PaperBroker):
        raise HTTPException(400, "ペーパー口座ではありません")
    # 建玉を消す前に記録側も閉じる(開いたままの記録が残ると上限判定が狂う)
    for row in tradestore.open_trades("paper"):
        tradestore.mark_closed(row["id"], row["entry_price"],
                               datetime.now(timezone.utc).isoformat(),
                               0.0, 0.0, "ペーパー口座のリセット")
    b.reset(balance)
    return {"ok": True, "balance": balance}


@app.get("/api/trade/auto-status")
def auto_status():
    """自動エントリーの状態。UIの表示に使う。"""
    cfg = config.load()
    try:
        env = brokermod.get_broker().env
    except Exception:
        env = "?"
    return {
        "enabled": bool(cfg.get("auto_entry")),
        "paper_only": trading.AUTO_ENTRY_PAPER_ONLY,
        "env": env,
        "blocked_by_env": trading.AUTO_ENTRY_PAPER_ONLY and env != "paper",
        "symbols": cfg.get("auto_symbols"), "interval": cfg.get("auto_interval"),
        "min_prob": cfg.get("auto_min_prob"), "min_score": cfg.get("auto_min_score"),
        "min_rr": cfg.get("auto_min_rr"), "skip_news": cfg.get("auto_skip_news"),
        "last_run": cfg.get("auto_last_run"), "log": cfg.get("auto_log"),
    }


class SettingsPatch(BaseModel):
    auto_train: bool | None = None
    auto_train_days: int | None = None
    background_predict: bool | None = None
    # 売買執行
    trading_enabled: bool | None = None
    broker: str | None = None
    kill_switch: bool | None = None
    risk_pct: float | None = None
    max_units: int | None = None
    max_positions: int | None = None
    one_per_symbol: bool | None = None
    daily_loss_limit_pct: float | None = None
    manage_interval_sec: int | None = None
    sl_atr_mult: float | None = None
    breakeven_atr: float | None = None
    breakeven_offset_pips: float | None = None
    trail_mode: str | None = None
    trail_atr_mult: float | None = None
    exit_on_flip: bool | None = None
    close_before_news: bool | None = None
    # 自動エントリー
    auto_entry: bool | None = None
    auto_symbols: str | None = None
    auto_interval: str | None = None
    auto_min_prob: float | None = None
    auto_min_score: int | None = None
    auto_min_rr: float | None = None
    auto_skip_news: bool | None = None


@app.get("/api/settings")
def get_settings():
    return config.load()


@app.post("/api/settings")
def post_settings(body: SettingsPatch):
    patch = body.model_dump(exclude_none=True)
    if patch.get("broker") not in (None, "paper", "oanda"):
        raise HTTPException(400, "broker は paper か oanda")
    if patch.get("trail_mode") not in (None, "atr", "fixed", "swing", "off"):
        raise HTTPException(400, "trail_mode は atr / fixed / swing / off")
    cfg = config.save(patch)
    if "broker" in patch:
        brokermod.reset_broker()
    return cfg


@app.get("/api/calendar")
def calendar_events(symbol: str = "USD_JPY"):
    try:
        events = econcal.events_for(symbol)
    except Exception as e:
        return {"events": [], "warning": False, "error": str(e)}
    return {"events": events,
            "warning": any(x["in_window"] for x in events)}


@app.get("/api/model-info")
def model_info(symbol: str = "USD_JPY", interval: str = "15m"):
    loaded = model.load(symbol, gmo.INTERVAL_MAP.get(interval, interval))
    if loaded is None:
        raise HTTPException(404, "モデル未学習")
    return loaded[1]
