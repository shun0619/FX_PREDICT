"use client";

import { useEffect, useState } from "react";
import EventBanner, { Calendar } from "@/components/EventBanner";
import { INTERVALS, SYMBOLS, getJSON, postJSON, saveSettings, usePoll, useSettings } from "@/lib/api";

type BrokerStatus = {
  broker: string; env: string; is_live: boolean; live_allowed: boolean;
  oanda_configured: boolean; connected: boolean; error: string | null;
  server_stops: boolean; open_count: number;
  account: {
    currency: string; balance: number; equity: number;
    margin_used: number; margin_available: number; unrealized_pl: number;
  } | null;
  manage: { last_run: string | null; last_error: string | null };
};

type Plan = {
  symbol: string; side: string; price: number; bid: number; ask: number;
  spread_pips: number; sl: number; tp: number; sl_pips: number; tp_pips: number;
  rr: number; atr: number; units: number; risk_amount: number; risk_ccy: number;
  capped: boolean; too_small: boolean; currency: string; equity: number;
  blocks: string[]; server_stops: boolean; is_live: boolean;
};

type OpenTrade = {
  id: number; symbol: string; interval: string; side: string; units: number;
  entry_price: number; entry_time: string; initial_sl: number; tp: number;
  current_sl: number; be_done: number; price?: number; pnl_pips?: number;
  pnl_ccy?: number; sl_distance_pips?: number; trailing?: boolean; error?: string;
};

type LogEvent = {
  at: string; id?: number; sl?: number; note?: string; reason?: string;
  error?: string; closed?: boolean;
  // 自動エントリー由来のイベント
  auto?: boolean; symbol?: string; skip?: string; opened?: boolean;
  side?: string; prob?: number; score?: number; units?: number; entry?: number;
};

type OpenResp = {
  trades: OpenTrade[];
  log: LogEvent[];
  manage: { last_run: string | null; last_error: string | null };
};

type HistoryResp = {
  env: string;
  items: {
    id: number; symbol: string; side: string; units: number; entry_price: number;
    close_price: number; close_time: string; pnl_pips: number; pnl_ccy: number;
    close_reason: string;
  }[];
  stats: { count: number; pnl_ccy: number; pnl_pips: number; win_rate: number | null; profit_factor: number | null };
};

type EntryPlan = {
  direction: string; prob_up: number; confidence: number;
  score: number; level: "good" | "fair" | "poor";
  components: { key: string; label: string; pct: number; note?: string }[];
};

const LEVEL_STYLE = {
  good: { color: "text-up", label: "良い", msg: "条件が揃っています。それでも損切りは必ず置くこと。" },
  fair: { color: "text-amber-400", label: "普通", msg: "決め手に欠けます。急いで入る場面ではありません。" },
  poor: { color: "text-down", label: "悪い", msg: "今は見送りが無難です。" },
} as const;

type AutoStatus = {
  enabled: boolean; paper_only: boolean; env: string; blocked_by_env: boolean;
  symbols: string; interval: string; min_prob: number; min_score: number; min_rr: number;
  last_run: string | null; log: string | null;
};

const yen = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "" : "-"}${Math.abs(Math.round(v)).toLocaleString()}`;

export default function Trade() {
  const [s] = useSettings();
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  const [v, setV] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmLive, setConfirmLive] = useState(false);

  // 手入力の上書き値 (null なら自動計算値をそのまま使う)
  const [units, setUnits] = useState<string>("");
  const [sl, setSl] = useState<string>("");
  const [tp, setTp] = useState<string>("");

  const { data: st } = usePoll<BrokerStatus>(`/api/broker/status?v=${v}`, 10);
  const { data: plan, error: planErr } = usePoll<Plan>(
    `/api/trade/plan?symbol=${s.symbol}&interval=${s.interval}&side=${side}&v=${v}`, 10);
  const { data: open } = usePoll<OpenResp>(`/api/trade/open?v=${v}`, 5);
  const { data: hist } = usePoll<HistoryResp>(`/api/trade/history?limit=30&v=${v}`, 15);
  const { data: ep } = usePoll<EntryPlan>(
    `/api/entry-plan?symbol=${s.symbol}&interval=${s.interval}`, 15);
  const { data: cal } = usePoll<Calendar>(`/api/calendar?symbol=${s.symbol}`, 600);
  const newsWarn = cal?.warning ?? false;
  const { data: auto } = usePoll<AutoStatus>(`/api/trade/auto-status?v=${v}`, 15);

  // 方向・通貨ペアを変えたら手入力をリセット
  useEffect(() => { setUnits(""); setSl(""); setTp(""); }, [side, s.symbol, s.interval]);

  const digits = s.symbol.endsWith("_JPY") ? 3 : 5;
  const blocked = (plan?.blocks?.length ?? 0) > 0;
  const live = st?.is_live ?? false;

  const act = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); } catch (e: any) { setErr(e.message); }
    setBusy(false); setV((x) => x + 1);
    setTimeout(() => setMsg(null), 4000);
  };

  const submit = () => act(async () => {
    await postJSON("/api/trade/open", {
      symbol: s.symbol, interval: s.interval, side,
      units: units === "" ? null : Number(units),
      sl: sl === "" ? null : Number(sl),
      tp: tp === "" ? null : Number(tp),
      confirm: confirmLive,
    });
  }, "発注しました。ここから先の損切り・利確は自動です");

  return (
    <div className="space-y-3">
      {/* 自動売買が動いているときの表示 */}
      {auto?.enabled && !auto.blocked_by_env && (
        <div className="rounded-xl border border-accent/50 bg-accent/10 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="flex items-center gap-2 text-sm font-bold text-accent">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
              自動売買 稼働中
            </span>
            <span className="text-[11px] text-gray-300">
              {auto.symbols} / {auto.interval} / 確率{Math.round(auto.min_prob * 100)}%以上 ・ スコア{auto.min_score}点以上
              {auto.min_rr > 0 && ` ・ 損益バランス1:${auto.min_rr}以上`}
            </span>
            <a href="/settings" className="ml-auto text-[10px] text-muted underline">条件を変える</a>
          </div>
          {auto.log && (
            <div className="mt-1 text-[10px] text-muted">
              最終判定: {auto.log}
              {auto.last_run && ` (${new Date(auto.last_run).toLocaleTimeString("ja-JP")})`}
            </div>
          )}
        </div>
      )}
      {auto?.enabled && auto.blocked_by_env && (
        <div className="rounded-xl border border-down/50 bg-down/10 px-4 py-3 text-sm text-down">
          ⚠ 自動売買がオンですが、接続先({auto.env})では動作しません。
          ペーパー口座に切り替えてください
        </div>
      )}

      {/* 重要経済指標のアラート */}
      <EventBanner />

      {/* 本番口座の警告 */}
      {live && (
        <div className="rounded-xl border border-down/60 bg-down/10 px-4 py-3">
          <div className="text-sm font-bold text-down">⚠ 本番口座に接続しています</div>
          <p className="mt-0.5 text-[11px] text-gray-300">
            ここでの発注は実際の資金が動きます。数量・損切りを毎回確認してください。
          </p>
        </div>
      )}

      {/* 口座サマリー */}
      <div className="card grid gap-4 md:grid-cols-[1fr_auto]">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          <div>
            <div className="card-title mb-1">接続先</div>
            <div className="text-sm font-semibold text-white">
              {st?.broker === "paper" ? "ペーパー口座"
                : st?.env === "live" ? "OANDA 本番" : "OANDA デモ"}
            </div>
            <div className={`text-[10px] ${st?.connected ? "text-up" : "text-down"}`}>
              {st?.connected ? "接続OK" : st?.error ?? "未接続"}
            </div>
          </div>
          <div>
            <div className="card-title mb-1">有効証拠金</div>
            <div className="font-mono text-lg text-white">
              {yen(st?.account?.equity)}
              <span className="ml-1 text-[10px] font-sans text-muted">{st?.account?.currency}</span>
            </div>
            <div className="text-[10px] text-muted">残高 {yen(st?.account?.balance)}</div>
          </div>
          <div>
            <div className="card-title mb-1">建玉</div>
            {st?.open_count ? (
              <>
                <div className={`font-mono text-lg leading-7 ${(st?.account?.unrealized_pl ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                  {yen(st?.account?.unrealized_pl)}
                </div>
                <div className="text-[10px] text-muted">{st.open_count}件の評価損益</div>
              </>
            ) : (
              <>
                <div className="text-lg leading-7 text-gray-400">なし</div>
                <div className="text-[10px] text-muted">建玉を持つと損益が出ます</div>
              </>
            )}
          </div>
          <div>
            <div className="card-title mb-1">損切りの置き場所</div>
            <div className={`text-sm font-semibold ${st?.server_stops ? "text-up" : "text-gray-200"}`}>
              {st?.server_stops ? "ブローカー側" : "このアプリ側"}
            </div>
            <div className="text-[10px] leading-tight text-muted">
              {st?.server_stops
                ? "アプリが落ちても損切りは残ります"
                : "アプリを止めると損切りも止まります"}
            </div>
          </div>
        </div>
        <div className="flex flex-col justify-center gap-2">
          <button disabled={busy || !(open?.trades.length)}
            onClick={() => { if (confirm("全建玉を成行で決済します。よろしいですか?")) act(() => postJSON("/api/trade/close-all"), "全建玉を決済しました"); }}
            className="rounded-lg border border-down/50 bg-down/15 px-4 py-2 text-xs font-semibold text-down disabled:opacity-40">
            全決済 (緊急停止)
          </button>
          <a href="/settings" className="text-center text-[10px] text-muted underline">売買設定を開く</a>
        </div>
      </div>

      {msg && <div className="rounded-lg border border-up/40 bg-up/10 px-3 py-2 text-xs text-up">{msg}</div>}
      {err && <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down">{err}</div>}

      {/* エントリー判定 (AIの見立て) */}
      {ep && (
        <div className="card">
          <div className="grid gap-6 md:grid-cols-[260px_1fr]">
            <div>
              <div className="card-title">エントリー判定</div>
              <div className="flex items-baseline gap-2">
                <span className={`text-4xl font-bold leading-none ${LEVEL_STYLE[ep.level].color}`}>{ep.score}</span>
                <span className="text-xs text-muted">/100</span>
                <span className={`ml-1 text-lg font-semibold ${LEVEL_STYLE[ep.level].color}`}>
                  {LEVEL_STYLE[ep.level].label}
                </span>
              </div>
              <div className="mt-2.5 space-y-1 text-xs">
                <div className="text-[10px] uppercase tracking-wider text-muted">AIの見立て</div>
                {(() => {
                  const pct = Math.round(
                    (ep.direction === "UP" ? ep.prob_up : 1 - ep.prob_up) * 100);
                  // 55%未満は方向を断言しない (ほぼ五分五分のため)
                  if (pct < 55) {
                    return (
                      <div className="text-gray-300">
                        判断保留 <span className="text-muted">— どちらとも言えない ({pct}%)</span>
                      </div>
                    );
                  }
                  return (
                    <div className={`font-semibold ${ep.direction === "UP" ? "text-up" : "text-down"}`}>
                      {ep.direction === "UP" ? "▲ 上昇" : "▼ 下落"} {pct}%
                    </div>
                  );
                })()}
              </div>
              <p className="mt-2.5 text-[11px] leading-relaxed text-gray-400">{LEVEL_STYLE[ep.level].msg}</p>
              {/* 発注方向とAIの向きが逆のときは警告。
                  ただし確率55%未満は「判断保留」扱いなので警告しない */}
              {Math.max(ep.prob_up, 1 - ep.prob_up) >= 0.55 &&
                ((side === "LONG" && ep.direction === "DOWN") ||
                 (side === "SHORT" && ep.direction === "UP")) && (
                <div className="mt-2.5 rounded-lg border border-amber-400/40 bg-amber-400/10 px-2.5 py-1.5 text-[10px] leading-relaxed text-amber-400">
                  ⚠ 選択中の方向はAIの見立てと逆です
                </div>
              )}
            </div>
            <div className="md:border-l md:border-line md:pl-6">
              <div className="card-title">スコアの内訳</div>
              <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                {ep.components.map((c) => {
                  const col = c.pct >= 60 ? "bg-up" : c.pct >= 40 ? "bg-amber-400" : "bg-down";
                  return (
                    <div key={c.key}>
                      <div className="mb-1 flex items-baseline justify-between gap-2 text-[11px]">
                        <span className="truncate text-gray-300">
                          {c.label}
                          {c.note && <span className="ml-1 text-[9px] text-muted">({c.note})</span>}
                        </span>
                        <span className="shrink-0 font-mono text-muted">{c.pct}%</span>
                      </div>
                      <div className="h-1.5 rounded bg-panel2">
                        <div className={`h-full rounded ${col}`} style={{ width: `${c.pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
              <p className="mt-3 text-[9px] text-muted">
                配点: AI信頼度40 + バンド位置20 + RSI位置20 + 過去実績20
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 発注パネル */}
      <div className="card space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="card-title mb-0">発注</div>
          <select value={s.symbol} onChange={(e) => saveSettings({ ...s, symbol: e.target.value })}
            className="rounded-lg border border-line bg-panel2 px-2 py-1 text-sm font-semibold text-white">
            {SYMBOLS.map((x) => <option key={x} value={x}>{x.replace("_", "/")}</option>)}
          </select>
          <div className="flex gap-1">
            {INTERVALS.map((tf) => (
              <button key={tf} onClick={() => saveSettings({ ...s, interval: tf })}
                className={`rounded-md px-2.5 py-1 text-[11px] ${s.interval === tf ? "bg-accent text-white" : "bg-panel2 text-muted"}`}>
                {tf}
              </button>
            ))}
          </div>
          <div className="ml-auto flex gap-1">
            <button onClick={() => setSide("LONG")}
              className={`rounded-md px-4 py-1.5 text-xs font-bold ${side === "LONG" ? "bg-up/25 text-up border border-up/50" : "bg-panel2 text-muted border border-line"}`}>
              ▲ 買い
            </button>
            <button onClick={() => setSide("SHORT")}
              className={`rounded-md px-4 py-1.5 text-xs font-bold ${side === "SHORT" ? "bg-down/25 text-down border border-down/50" : "bg-panel2 text-muted border border-line"}`}>
              ▼ 売り
            </button>
          </div>
        </div>

        {planErr && <p className="text-xs text-down">{planErr}</p>}
        {plan && (
          <>
            <div className="grid gap-5 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1fr)]">
              {/* 価格 */}
              <div className="space-y-2 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="text-muted">想定約定価格</span>
                  <span className="font-mono text-white">{plan.price.toFixed(digits)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted">スプレッド</span>
                  <span className="font-mono text-gray-300">{plan.spread_pips} pips</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted">ATR ({s.interval})</span>
                  <span className="font-mono text-gray-300">{plan.atr}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted">損益バランス</span>
                  <span className={`font-mono ${plan.rr >= 1 ? "text-up" : "text-amber-400"}`}>1 : {plan.rr}</span>
                </div>
              </div>

              {/* 数量・SL・TP (自動計算値を上書き可) */}
              <div className="space-y-2.5 text-xs">
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-muted">数量 (通貨単位)</span>
                  <input inputMode="numeric" value={units} placeholder={String(plan.units)}
                    onChange={(e) => setUnits(e.target.value.replace(/[^0-9]/g, ""))}
                    className="mt-1 w-full rounded-md border border-line bg-panel2 px-2 py-1.5 font-mono text-white
                               placeholder:text-gray-400 focus:border-accent focus:outline-none" />
                  <span className="mt-0.5 block text-[9px] text-muted">
                    リスク{plan.risk_amount ? yen(plan.risk_amount) : "—"}円から自動計算
                    {plan.capped && " (上限で丸め)"}
                  </span>
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-muted">損切り</span>
                    <input inputMode="decimal" value={sl} placeholder={String(plan.sl)}
                      onChange={(e) => setSl(e.target.value)}
                      className="mt-1 w-full rounded-md border border-line bg-panel2 px-2 py-1.5 font-mono text-pink-400
                                 placeholder:text-pink-400/60 focus:border-accent focus:outline-none" />
                    <span className="mt-0.5 block text-[9px] text-muted">{plan.sl_pips} pips</span>
                  </label>
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-muted">利確</span>
                    <input inputMode="decimal" value={tp} placeholder={String(plan.tp)}
                      onChange={(e) => setTp(e.target.value)}
                      className="mt-1 w-full rounded-md border border-line bg-panel2 px-2 py-1.5 font-mono text-cyan-400
                                 placeholder:text-cyan-400/60 focus:border-accent focus:outline-none" />
                    <span className="mt-0.5 block text-[9px] text-muted">{plan.tp_pips} pips</span>
                  </label>
                </div>
              </div>

              {/* 発注 */}
              <div className="space-y-2">
                <div className="rounded-lg bg-panel2 p-2.5 text-[11px] leading-relaxed">
                  <div className="flex justify-between">
                    <span className="text-muted">この取引の想定最大損失</span>
                    <span className="font-mono text-down">-{yen(plan.risk_ccy)} {plan.currency}</span>
                  </div>
                  <div className="mt-0.5 text-[9px] text-muted">
                    損切りに当たった場合の金額。有効証拠金の
                    {plan.equity > 0 ? ` ${(plan.risk_ccy / plan.equity * 100).toFixed(2)}%` : " —"}
                  </div>
                </div>

                {plan.too_small && !units && (
                  <p className="text-[10px] text-amber-400">
                    計算上の数量が最小単位(1,000)未満です。リスク%を上げるか損切り幅を狭めてください。
                  </p>
                )}
                {blocked && (
                  <ul className="space-y-0.5 text-[10px] text-amber-400">
                    {plan.blocks.map((b, i) => <li key={i}>・{b}</li>)}
                  </ul>
                )}
                {live && (
                  <label className="flex items-start gap-2 text-[10px] text-down">
                    <input type="checkbox" checked={confirmLive} className="mt-0.5"
                      onChange={(e) => setConfirmLive(e.target.checked)} />
                    実資金が動くことを確認しました
                  </label>
                )}
                {/* スコアが低い/AIと逆方向/指標前 のときはボタンを主張させない */}
                {(() => {
                  const weak = ep ? ep.level === "poor" : false;
                  // 55%未満は「判断保留」なので方向の逆行判定はしない
                  const decisive = ep ? Math.max(ep.prob_up, 1 - ep.prob_up) >= 0.55 : false;
                  const against = ep && decisive &&
                    ((side === "LONG" && ep.direction === "DOWN") ||
                     (side === "SHORT" && ep.direction === "UP"));
                  const dim = weak || against || newsWarn;
                  const color = side === "LONG"
                    ? (dim ? "bg-panel2 border border-up/40 text-up hover:bg-up/20"
                           : "bg-up/80 hover:bg-up text-white")
                    : (dim ? "bg-panel2 border border-down/40 text-down hover:bg-down/20"
                           : "bg-down/80 hover:bg-down text-white");
                  return (
                    <>
                      <button disabled={busy || blocked || (live && !confirmLive)}
                        onClick={submit}
                        className={`w-full rounded-lg py-3 text-sm font-bold transition disabled:opacity-40 ${color}`}>
                        {side === "LONG" ? "▲ 買いでエントリー" : "▼ 売りでエントリー"}
                      </button>
                      {dim && (
                        <p className="mt-1.5 text-center text-[10px] leading-relaxed text-amber-400">
                          {newsWarn ? "重要経済指標の前後です — 値動きが荒れやすい場面です"
                            : weak ? `判定 ${ep!.score}点 — 条件が良くない場面です`
                            : "AIの見立てと逆方向です"}
                        </p>
                      )}
                    </>
                  );
                })()}
                <p className="text-[9px] leading-relaxed text-muted">
                  発注と同時に損切り・利確が設置されます。以降の建値ストップとトレーリングは自動です。
                </p>
              </div>
            </div>
          </>
        )}
      </div>

      {/* 建玉 (無いときは表示しない) */}
      <div className={open?.trades.length ? "card" : "hidden"}>
        <div className="card-title">保有中の建玉 (自動決済の監視中)</div>
        <div className="space-y-2">
          {open?.trades.map((t) => {
            const d = t.symbol.endsWith("_JPY") ? 3 : 5;
            return (
              <div key={t.id} className="rounded-lg border border-line bg-panel2 p-3">
                <div className="grid items-center gap-3 md:grid-cols-[160px_1fr_1fr_auto]">
                  <div>
                    <span className={`text-sm font-bold ${t.side === "LONG" ? "text-up" : "text-down"}`}>
                      {t.side === "LONG" ? "▲ 買い" : "▼ 売り"}
                    </span>
                    <span className="ml-2 text-xs text-white">{t.symbol.replace("_", "/")}</span>
                    <div className="text-[10px] text-muted">
                      {t.units.toLocaleString()} 通貨 @ {t.entry_price.toFixed(d)}
                    </div>
                  </div>
                  <div>
                    <div className={`font-mono text-2xl ${(t.pnl_pips ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                      {(t.pnl_pips ?? 0) >= 0 ? "+" : ""}{t.pnl_pips ?? "—"}
                      <span className="ml-1 text-[10px] font-sans text-muted">pips</span>
                    </div>
                    <div className={`text-xs font-mono ${(t.pnl_ccy ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                      {yen(t.pnl_ccy)} 円
                    </div>
                  </div>
                  <div className="space-y-1 text-[11px]">
                    <div className="flex justify-between gap-3">
                      <span className="text-muted">
                        {t.trailing ? "トレーリングSL" : "損切り"}
                      </span>
                      <span className="font-mono text-pink-400">
                        {(t.current_sl ?? t.initial_sl)?.toFixed(d)}
                        {t.sl_distance_pips != null && (
                          <span className="ml-1 text-[9px] text-muted">({t.sl_distance_pips}p)</span>
                        )}
                      </span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-muted">利確</span>
                      <span className="font-mono text-cyan-400">{t.tp?.toFixed(d)}</span>
                    </div>
                    {t.trailing && (
                      <div className="text-[9px] text-up">
                        {t.be_done ? "建値ストップ済み — この取引の負けはほぼ消えました" : "含み益に追従してSLを切り上げ中"}
                      </div>
                    )}
                    {t.error && <div className="text-[9px] text-down">{t.error}</div>}
                  </div>
                  <button disabled={busy}
                    onClick={() => act(() => postJSON(`/api/trade/close?id=${t.id}`), "決済しました")}
                    className="rounded-lg bg-accent px-4 py-2 text-xs font-medium text-white disabled:opacity-40">
                    今すぐ決済
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        {open?.manage.last_error && (
          <p className="mt-2 text-[10px] text-down">監視エラー: {open.manage.last_error}</p>
        )}
      </div>

      {/* 履歴 */}
      <div className="card">
        <div className="flex flex-wrap items-baseline gap-4">
          <div className="card-title mb-0">取引履歴 ({hist?.env})</div>
          {hist?.stats && hist.stats.count > 0 && (
            <div className="flex gap-4 text-[11px] text-muted">
              <span>{hist.stats.count}件</span>
              <span>勝率 <span className="text-gray-200">{hist.stats.win_rate ?? "—"}%</span></span>
              <span>PF <span className="text-gray-200">{hist.stats.profit_factor ?? "—"}</span></span>
              <span>通算 <span className={hist.stats.pnl_ccy >= 0 ? "text-up" : "text-down"}>
                {yen(hist.stats.pnl_ccy)}円 ({hist.stats.pnl_pips >= 0 ? "+" : ""}{hist.stats.pnl_pips}p)
              </span></span>
            </div>
          )}
        </div>
        {!hist?.items.length && <p className="py-4 text-center text-xs text-muted">まだ決済履歴はありません</p>}
        {!!hist?.items.length && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead className="text-muted">
                <tr className="border-b border-line">
                  <th className="py-1.5 text-left font-normal">決済時刻</th>
                  <th className="text-left font-normal">ペア</th>
                  <th className="text-left font-normal">方向</th>
                  <th className="text-right font-normal">建値</th>
                  <th className="text-right font-normal">決済値</th>
                  <th className="text-right font-normal">pips</th>
                  <th className="text-right font-normal">損益</th>
                  <th className="pl-3 text-left font-normal">決済理由</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {hist.items.map((r) => (
                  <tr key={r.id} className="border-b border-line/50">
                    <td className="py-1.5 text-muted">
                      {new Date(r.close_time).toLocaleString("ja-JP", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                    </td>
                    <td>{r.symbol.replace("_", "/")}</td>
                    <td className={r.side === "LONG" ? "text-up" : "text-down"}>
                      {r.side === "LONG" ? "買" : "売"}
                    </td>
                    <td className="text-right">{r.entry_price}</td>
                    <td className="text-right">{r.close_price}</td>
                    <td className={`text-right ${r.pnl_pips >= 0 ? "text-up" : "text-down"}`}>
                      {r.pnl_pips >= 0 ? "+" : ""}{r.pnl_pips}
                    </td>
                    <td className={`text-right ${r.pnl_ccy >= 0 ? "text-up" : "text-down"}`}>{yen(r.pnl_ccy)}</td>
                    <td className="pl-3 font-sans text-muted">{r.close_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>


      {/* 決済エンジンのログ */}
      {!!open?.log.length && (
        <div className="card">
          <div className="card-title">自動売買エンジンの動き</div>
          <ul className="space-y-0.5 text-[10px] text-muted">
            {open.log.slice(0, 12).map((e, i) => {
              const time = new Date(e.at).toLocaleTimeString("ja-JP");
              let text: string;
              let cls = "";
              if (e.error) {
                text = `エラー: ${e.error}`; cls = "text-down";
              } else if (e.opened) {
                text = `【発注】${e.symbol?.replace("_", "/")} ${e.side === "LONG" ? "買い" : "売り"} `
                     + `${e.units?.toLocaleString()}通貨 @${e.entry} `
                     + `(確率${Math.round((e.prob ?? 0) * 100)}% / スコア${e.score})`;
                cls = "text-accent";
              } else if (e.skip) {
                text = `見送り: ${e.symbol?.replace("_", "/")} — ${e.skip}`;
              } else if (e.closed) {
                text = `#${e.id} 決済 (${e.reason})`; cls = "text-cyan-400";
              } else if (e.sl != null) {
                text = `#${e.id} SL → ${e.sl} (${e.note})`;
              } else {
                return null;   // 表示できる情報がないイベントは出さない
              }
              return <li key={i} className={cls}>{time} ・ {text}</li>;
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
