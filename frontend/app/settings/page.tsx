"use client";

import { useEffect, useState } from "react";
import { INTERVALS, useSettings, getJSON, postJSON } from "@/lib/api";

type ServerSettings = {
  auto_train: boolean;
  auto_train_days: number;
  last_auto_train: string | null;
  auto_train_log: string | null;
  background_predict: boolean;
  // 売買執行
  trading_enabled: boolean;
  broker: "paper" | "oanda";
  kill_switch: boolean;
  risk_pct: number;
  max_units: number;
  max_positions: number;
  one_per_symbol: boolean;
  daily_loss_limit_pct: number;
  manage_interval_sec: number;
  sl_atr_mult: number;
  breakeven_atr: number;
  breakeven_offset_pips: number;
  trail_mode: "atr" | "off";
  trail_atr_mult: number;
  exit_on_flip: boolean;
  close_before_news: boolean;
  auto_entry: boolean;
  auto_symbols: string;
  auto_interval: string;
  auto_min_prob: number;
  auto_min_score: number;
  auto_min_rr: number;
  auto_skip_news: boolean;
};

type AutoStatus = {
  enabled: boolean; paper_only: boolean; env: string; blocked_by_env: boolean;
  last_run: string | null; log: string | null;
};

type BrokerStatus = {
  broker: string; env: string; is_live: boolean; live_allowed: boolean;
  oanda_configured: boolean; connected: boolean; error: string | null;
  account: { currency: string; balance: number; equity: number } | null;
};

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? "bg-up" : "bg-line"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

function Num({ label, value, step, unit, hint, onChange }: {
  label: string; value: number; step?: number; unit?: string; hint?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block text-xs">
      <span className="text-muted">{label}</span>
      <span className="mt-0.5 flex items-center gap-1">
        <input type="number" step={step ?? 1} defaultValue={value}
          onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v) && v !== value) onChange(v); }}
          className="w-full rounded-md border border-line bg-panel2 px-2 py-1.5 font-mono text-white" />
        {unit && <span className="text-[10px] text-muted">{unit}</span>}
      </span>
      {hint && <span className="mt-0.5 block text-[9px] leading-relaxed text-muted">{hint}</span>}
    </label>
  );
}

export default function Settings() {
  const [s, save] = useSettings();
  const [saved, setSaved] = useState(false);
  const [srv, setSrv] = useState<ServerSettings | null>(null);
  const [srvErr, setSrvErr] = useState<string | null>(null);
  const [bs, setBs] = useState<BrokerStatus | null>(null);

  const [au, setAu] = useState<AutoStatus | null>(null);

  const reloadBroker = () => {
    getJSON<BrokerStatus>("/api/broker/status").then(setBs).catch(() => setBs(null));
    getJSON<AutoStatus>("/api/trade/auto-status").then(setAu).catch(() => setAu(null));
  };

  useEffect(() => {
    getJSON<ServerSettings>("/api/settings").then(setSrv).catch((e) => setSrvErr(e.message));
    reloadBroker();
    const id = setInterval(reloadBroker, 20000);
    return () => clearInterval(id);
  }, []);

  const patchSrv = async (patch: Partial<ServerSettings>) => {
    try {
      setSrv(await postJSON<ServerSettings>("/api/settings", patch));
      setSrvErr(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      if ("broker" in patch) reloadBroker();
    } catch (e: any) {
      setSrvErr(e.message);
    }
  };
  const set = (patch: Partial<typeof s>) => {
    save({ ...s, ...patch });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="max-w-2xl space-y-3">
      <h1 className="text-xl font-bold text-white">設定</h1>
      <p className="text-xs text-muted">
        通貨ペア・時間足・表示指標の切り替えはチャート画面に移動しました。
      </p>

      <div className="card space-y-3">
        <div className="card-title">更新間隔</div>
        <div className="flex gap-1">
          {[5, 15, 30, 60].map((v) => (
            <button key={v} onClick={() => set({ refreshSec: v })}
              className={`rounded-md px-4 py-2 text-xs font-medium
                ${s.refreshSec === v ? "bg-accent text-white" : "bg-panel2 text-muted"}`}>{v}秒</button>
          ))}
        </div>
        <p className="text-[10px] text-muted">GMOコインAPIは公開APIのため常識的な間隔で。5秒は1m足スキャル向け。</p>
      </div>

      <div className="card space-y-3">
        <div className="card-title">常時予測 (履歴の自動記録)</div>
        {srv && (
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm">ブラウザを閉じていても予測履歴を記録し続ける</div>
              <p className="text-[10px] text-muted mt-0.5">
                バックエンド起動中、学習済みの全モデルで約1分ごとに予測して記録します。
                実測正解率の検証が早く進みます
              </p>
            </div>
            <button onClick={() => patchSrv({ background_predict: !srv.background_predict })}
              className={`relative h-6 w-11 rounded-full transition ${srv.background_predict ? "bg-up" : "bg-line"}`}>
              <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all
                ${srv.background_predict ? "left-[22px]" : "left-0.5"}`} />
            </button>
          </div>
        )}
      </div>

      {/* ------------------- 売買執行 ------------------- */}
      {srv && (
        <div className="card space-y-4">
          <div className="card-title">売買執行 (手動エントリー → 自動決済)</div>

          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm">発注機能を有効にする</div>
              <p className="mt-0.5 text-[10px] leading-relaxed text-muted">
                オフの間は取引ページから発注できません。既に建っている玉の自動決済は
                オフにしても動き続けます(損切りが外れる方が危険なため)
              </p>
            </div>
            <Toggle on={srv.trading_enabled} onClick={() => patchSrv({ trading_enabled: !srv.trading_enabled })} />
          </div>

          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm text-down">キルスイッチ</div>
              <p className="mt-0.5 text-[10px] text-muted">オンの間は新規発注を全面停止します</p>
            </div>
            <Toggle on={srv.kill_switch} onClick={() => patchSrv({ kill_switch: !srv.kill_switch })} />
          </div>

          <div className="border-t border-line pt-3">
            <div className="text-xs text-muted">接続先</div>
            <div className="mt-1.5 flex gap-1">
              {[
                { v: "paper", label: "ペーパー口座", note: "外部接続なし・練習用" },
                { v: "oanda", label: "OANDA", note: "デモ / 本番" },
              ].map((b) => (
                <button key={b.v} onClick={() => patchSrv({ broker: b.v as any })}
                  className={`flex-1 rounded-lg border px-3 py-2 text-left text-xs
                    ${srv.broker === b.v ? "border-accent bg-accent/15 text-white" : "border-line bg-panel2 text-muted"}`}>
                  <div className="font-semibold">{b.label}</div>
                  <div className="text-[9px]">{b.note}</div>
                </button>
              ))}
            </div>
            {bs && (
              <div className="mt-2 rounded-lg bg-panel2 p-2.5 text-[10px] leading-relaxed">
                <div className={bs.connected ? "text-up" : "text-down"}>
                  {bs.connected
                    ? `接続OK — ${bs.broker === "paper" ? "ペーパー" : bs.env === "live" ? "OANDA 本番" : "OANDA デモ"} / 残高 ${bs.account?.balance?.toLocaleString() ?? "—"} ${bs.account?.currency ?? ""}`
                    : `未接続: ${bs.error ?? "不明なエラー"}`}
                </div>
                {srv.broker === "oanda" && !bs.oanda_configured && (
                  <div className="mt-1 text-muted">
                    環境変数 <span className="font-mono text-gray-300">OANDA_TOKEN</span> と{" "}
                    <span className="font-mono text-gray-300">OANDA_ACCOUNT_ID</span> を設定してバックエンドを再起動してください。
                    デモは <span className="font-mono text-gray-300">OANDA_ENV=practice</span> (既定)。
                  </div>
                )}
                {bs.is_live && (
                  <div className="mt-1 font-semibold text-down">本番口座に接続中です</div>
                )}
              </div>
            )}
          </div>

          {/* リスク管理 */}
          <div className="border-t border-line pt-3">
            <div className="mb-2 text-xs font-semibold text-gray-200">リスク管理</div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Num label="1トレードのリスク" value={srv.risk_pct} step={0.1} unit="%"
                hint="有効証拠金に対する許容損失。損切り幅からロットを逆算します"
                onChange={(v) => patchSrv({ risk_pct: v })} />
              <Num label="1トレードの上限数量" value={srv.max_units} step={1000} unit="通貨"
                hint="計算結果がこれを超えたら丸めます"
                onChange={(v) => patchSrv({ max_units: v })} />
              <Num label="同時保有できる建玉数" value={srv.max_positions}
                onChange={(v) => patchSrv({ max_positions: v })} />
              <Num label="当日の損失上限" value={srv.daily_loss_limit_pct} step={0.5} unit="%"
                hint="到達したら新規発注を停止 (0で無効)。NY 17時で日替わり"
                onChange={(v) => patchSrv({ daily_loss_limit_pct: v })} />
            </div>
            <div className="mt-3 flex items-center justify-between gap-4">
              <div className="text-xs">同じ通貨ペアの重複建玉を禁止する</div>
              <Toggle on={srv.one_per_symbol} onClick={() => patchSrv({ one_per_symbol: !srv.one_per_symbol })} />
            </div>
          </div>

          {/* 自動決済 */}
          <div className="border-t border-line pt-3">
            <div className="mb-2 text-xs font-semibold text-gray-200">自動決済</div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Num label="初期の損切り幅" value={srv.sl_atr_mult} step={0.1} unit="× ATR"
                hint="建値からこの距離に損切りを置きます"
                onChange={(v) => patchSrv({ sl_atr_mult: v })} />
              <Num label="建値ストップへ移行する含み益" value={srv.breakeven_atr} step={0.1} unit="× ATR"
                hint="ここまで伸びたら損切りを建値へ。0で無効"
                onChange={(v) => patchSrv({ breakeven_atr: v })} />
              <Num label="建値ストップの上乗せ" value={srv.breakeven_offset_pips} step={0.1} unit="pips"
                hint="スプレッド分の負けを避けるため建値より少し有利側に置きます"
                onChange={(v) => patchSrv({ breakeven_offset_pips: v })} />
              <Num label="監視間隔" value={srv.manage_interval_sec} unit="秒"
                hint="短くすると反応が速くなりますがAPIリクエストが増えます"
                onChange={(v) => patchSrv({ manage_interval_sec: v })} />
            </div>

            <div className="mt-3 text-xs text-muted">トレーリングストップ (ATR追従)</div>
            <div className="mt-1.5 grid gap-3 sm:grid-cols-2">
              <div className="flex items-center justify-between rounded-lg border border-line bg-panel2 px-3 py-2">
                <span className="text-xs">含み益に応じてSLを自動で切り上げる</span>
                <Toggle on={srv.trail_mode === "atr"}
                  onClick={() => patchSrv({ trail_mode: srv.trail_mode === "atr" ? "off" : "atr" })} />
              </div>
              {srv.trail_mode === "atr" && (
                <Num label="追従の距離" value={srv.trail_atr_mult} step={0.1} unit="× ATR"
                  hint="建玉後の最高値(売りは最安値)からこの距離を保ってSLが動きます"
                  onChange={(v) => patchSrv({ trail_atr_mult: v })} />
              )}
            </div>

            <div className="mt-3 space-y-2.5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-xs">AI予測が2本連続で逆方向になったら決済する</div>
                  <p className="text-[9px] text-muted">SL/TPに届く前でも畳みます</p>
                </div>
                <Toggle on={srv.exit_on_flip} onClick={() => patchSrv({ exit_on_flip: !srv.exit_on_flip })} />
              </div>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-xs">重要な経済指標の直前に決済する</div>
                  <p className="text-[9px] text-muted">発表前後の急変とスプレッド拡大を避けます</p>
                </div>
                <Toggle on={srv.close_before_news} onClick={() => patchSrv({ close_before_news: !srv.close_before_news })} />
              </div>
            </div>

            {/* 自動エントリー */}
            <div className="mt-4 rounded-lg border border-line bg-panel2/50 p-3">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm font-semibold text-white">
                    自動エントリー (完全自動売買)
                  </div>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-muted">
                    条件を満たしたらAIの判断だけで発注します。決済は既存の自動ルール。
                    {au?.paper_only && (
                      <span className="block text-amber-400">
                        安全のためペーパー口座でのみ動作します(実口座はコード側でブロック)
                      </span>
                    )}
                  </p>
                </div>
                <Toggle on={srv.auto_entry}
                  onClick={() => patchSrv({ auto_entry: !srv.auto_entry })} />
              </div>

              {srv.auto_entry && (
                <>
                  {au?.blocked_by_env && (
                    <p className="mt-2 rounded border border-down/40 bg-down/10 px-2 py-1.5 text-[10px] text-down">
                      現在の接続先({au.env})では自動エントリーは動きません。ペーパー口座に切り替えてください
                    </p>
                  )}
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <label className="block text-xs">
                      <span className="text-muted">対象ペア (カンマ区切り)</span>
                      <input defaultValue={srv.auto_symbols}
                        onBlur={(e) => patchSrv({ auto_symbols: e.target.value })}
                        className="mt-0.5 w-full rounded-md border border-line bg-panel2 px-2 py-1.5 font-mono text-white" />
                      <span className="mt-0.5 block text-[9px] text-muted">例: USD_JPY,EUR_JPY</span>
                    </label>
                    <label className="block text-xs">
                      <span className="text-muted">対象の時間足</span>
                      <div className="mt-0.5 flex gap-1">
                        {INTERVALS.map((tf) => (
                          <button key={tf} onClick={() => patchSrv({ auto_interval: tf })}
                            className={`rounded-md px-3 py-1.5 text-[11px] font-medium
                              ${srv.auto_interval === tf ? "bg-accent text-white" : "bg-panel2 text-muted"}`}>
                            {tf}
                          </button>
                        ))}
                      </div>
                    </label>
                    <Num label="発注する最低確率" value={srv.auto_min_prob} step={0.01}
                      hint="0.65なら「上昇65%以上」または「下落65%以上」のときだけ発注"
                      onChange={(v) => patchSrv({ auto_min_prob: v })} />
                    <Num label="発注する最低スコア" value={srv.auto_min_score} unit="点"
                      hint="Entry Scoreがこの点数未満なら見送ります"
                      onChange={(v) => patchSrv({ auto_min_score: v })} />
                    <Num label="最低の損益バランス" value={srv.auto_min_rr} step={0.1}
                      hint="利確幅÷損切り幅。1.0未満は「勝っても負けのほうが大きい」設定なので既定で見送り(0で無効)"
                      onChange={(v) => patchSrv({ auto_min_rr: v })} />
                  </div>
                  <div className="mt-2.5 flex items-center justify-between gap-4">
                    <div className="text-xs">重要指標の警戒中は発注しない</div>
                    <Toggle on={srv.auto_skip_news}
                      onClick={() => patchSrv({ auto_skip_news: !srv.auto_skip_news })} />
                  </div>
                  <div className="mt-2 border-t border-line pt-2 text-[10px] text-muted">
                    <div>最終判定: {au?.last_run ? new Date(au.last_run).toLocaleString("ja-JP") : "未実行"}</div>
                    {au?.log && <div>結果: {au.log}</div>}
                  </div>
                </>
              )}
            </div>
          </div>

          <p className="border-t border-line pt-3 text-[10px] leading-relaxed text-muted">
            ペーパー口座では損切り・利確の発動判定をこのアプリが行うため、
            バックエンドを止めている間は自動決済も止まります。
            OANDAに接続した場合はSL/TPがブローカー側に置かれるので、
            PCが落ちても損切りは残ります。
          </p>
        </div>
      )}

      <div className="card space-y-3">
        <div className="card-title">自動再学習</div>
        {srvErr && <p className="text-xs text-down">バックエンドに接続できません: {srvErr}</p>}
        {srv && (
          <>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm">定期的にモデルを自動で再学習する</div>
                <p className="text-[10px] text-muted mt-0.5">
                  アプリ(バックエンド)起動中に、学習済みの全モデルを指定間隔で再学習します
                </p>
              </div>
              <button onClick={() => patchSrv({ auto_train: !srv.auto_train })}
                className={`relative h-6 w-11 rounded-full transition ${srv.auto_train ? "bg-up" : "bg-line"}`}>
                <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all
                  ${srv.auto_train ? "left-[22px]" : "left-0.5"}`} />
              </button>
            </div>
            {srv.auto_train && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-muted">間隔:</span>
                {[3, 7, 14].map((d) => (
                  <button key={d} onClick={() => patchSrv({ auto_train_days: d })}
                    className={`rounded-md px-3 py-1.5 font-medium
                      ${srv.auto_train_days === d ? "bg-accent text-white" : "bg-panel2 text-muted"}`}>
                    {d}日ごと
                  </button>
                ))}
              </div>
            )}
            <div className="text-[10px] text-muted space-y-0.5">
              <div>最終実行: {srv.last_auto_train
                ? new Date(srv.last_auto_train).toLocaleString("ja-JP") : "未実行(オンにすると30分以内に初回実行)"}</div>
              {srv.auto_train_log && <div>結果: {srv.auto_train_log}</div>}
            </div>
          </>
        )}
      </div>

      {saved && (
        <div className="fixed bottom-16 md:bottom-6 right-4 rounded-lg bg-up/90 px-4 py-2 text-sm font-medium text-white shadow-lg">
          ✓ 保存しました
        </div>
      )}

      <p className="text-[10px] leading-relaxed text-muted">
        本アプリは自分専用の情報分析ツールです。AI予測は過去データに基づく統計的推定であり、
        将来の値動きを保証するものではありません。投資判断は自己責任で行ってください。
      </p>
    </div>
  );
}
