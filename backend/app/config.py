"""サーバー側設定 (backend/data/settings.json)。"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"
_lock = Lock()

DEFAULTS = {
    "auto_train": False,        # 自動再学習のオンオフ
    "auto_train_days": 7,       # 再学習の間隔(日)
    "last_auto_train": None,    # 最終実行時刻 (ISO, UTC)
    "auto_train_log": None,     # 最終実行の結果メッセージ
    "background_predict": True, # 常時予測(ブラウザ不要で履歴を記録)

    # --- 半自動売買 (手動エントリー → 自動決済) ---
    "trading_enabled": False,   # 発注機能そのもののオンオフ。既定はオフ
    "broker": "paper",          # paper / oanda
    "kill_switch": False,       # オンにすると新規発注を全面停止

    # リスク管理
    "risk_pct": 1.0,            # 1トレードで許容する損失(有効証拠金に対する%)
    "max_units": 100_000,       # 1トレードの上限数量(通貨単位)
    "max_positions": 3,         # 同時保有できる建玉数
    "one_per_symbol": True,     # 同一ペアの重複建玉を禁止
    "daily_loss_limit_pct": 3.0,  # 当日損失がこの%に達したら新規発注を止める

    # 自動決済 (エグジット)
    "manage_interval_sec": 5,   # 決済エンジンの監視間隔(秒)
    "sl_atr_mult": 1.5,         # 初期SL = ATR × この倍率
    "breakeven_atr": 1.0,       # 含み益がATR×この倍率で建値ストップへ移行
    "breakeven_offset_pips": 1.0,  # 建値ストップの上乗せ(スプレッド負け防止)
    "trail_mode": "atr",        # atr / off
    "trail_atr_mult": 1.5,      # ATR追従の距離
    "exit_on_flip": False,      # AI予測が2本連続で逆方向になったら自動決済
    "close_before_news": False, # 重要指標の直前に自動決済する

    # --- 自動エントリー (ペーパー口座のみ。実口座はコード側でブロック) ---
    "auto_entry": False,           # 自動エントリーのオンオフ
    "auto_symbols": "USD_JPY",     # 対象ペア(カンマ区切り)
    "auto_interval": "1h",         # 対象の時間足
    "auto_min_prob": 0.65,         # この確率以上で発注 (下落は 1-この値 以下)
    "auto_min_score": 65,          # Entry Scoreがこの点数以上
    "auto_min_rr": 1.0,            # 損益バランス(利確幅÷損切り幅)がこの値以上
    "auto_skip_news": True,        # 重要指標の警戒中は発注しない
    "auto_last_run": None,         # 最終判定時刻 (ISO)
    "auto_log": None,              # 最終判定の結果メッセージ
}


# 安全範囲 (最小, 最大)。範囲外はクリップして保存する。
# 資金管理に直結するので「保存できてしまう」ことを許さない。
LIMITS: dict[str, tuple[float, float]] = {
    "auto_train_days": (1, 90),
    "risk_pct": (0.05, 5.0),          # 1トレードで有効証拠金の5%超は許可しない
    "max_units": (1_000, 10_000_000),
    "max_positions": (1, 20),
    "daily_loss_limit_pct": (0.0, 50.0),   # 0 = 無効
    "manage_interval_sec": (2, 300),
    "sl_atr_mult": (0.3, 10.0),       # 0だと損切り幅0で発注できなくなる
    "breakeven_atr": (0.0, 10.0),     # 0 = 建値ストップ無効
    "breakeven_offset_pips": (0.0, 100.0),
    "trail_atr_mult": (0.3, 10.0),
    "auto_min_prob": (0.5, 0.95),
    "auto_min_score": (0, 100),
    "auto_min_rr": (0.0, 5.0),
}
CHOICES: dict[str, tuple[str, ...]] = {
    "broker": ("paper", "oanda"),
    "trail_mode": ("atr", "off"),
    "auto_interval": ("15m", "1h", "4h"),
}


def _sanitize(cfg: dict) -> dict:
    """範囲外・型違いの値を安全な値に直す。"""
    out = dict(cfg)
    for k, (lo, hi) in LIMITS.items():
        try:
            v = float(out.get(k, DEFAULTS[k]))
        except (TypeError, ValueError):
            v = float(DEFAULTS[k])
        v = min(max(v, lo), hi)
        out[k] = int(v) if isinstance(DEFAULTS[k], int) else v
    for k, allowed in CHOICES.items():
        if out.get(k) not in allowed:
            out[k] = DEFAULTS[k]
    for k, dv in DEFAULTS.items():
        if isinstance(dv, bool) and not isinstance(out.get(k), bool):
            out[k] = dv
    return out


def load() -> dict:
    try:
        return _sanitize({**DEFAULTS, **json.loads(PATH.read_text(encoding="utf-8"))})
    except Exception:
        # 設定ファイルが壊れていても既定値で動き続ける
        return dict(DEFAULTS)


def save(patch: dict) -> dict:
    with _lock:
        cfg = load()
        cfg.update({k: v for k, v in patch.items() if k in DEFAULTS})
        cfg = _sanitize(cfg)
        PATH.parent.mkdir(parents=True, exist_ok=True)
        # 一時ファイル経由で置換し、書き込み中の破損を防ぐ
        tmp = PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(PATH)
        return cfg
