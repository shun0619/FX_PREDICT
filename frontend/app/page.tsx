"use client";

import PriceChart, { ChartData, PriceLine } from "@/components/PriceChart";
import EventBanner from "@/components/EventBanner";
import { INTERVALS, SYMBOLS, useSettings, usePoll, saveSettings } from "@/lib/api";

type EntryPlan = {
  direction: string; prob_up: number; confidence: number;
  entry: number; score: number; level: "good" | "fair" | "poor";
  components: { key: string; label: string; pct: number; note?: string }[];
  sl: number; tp: number; sl_pips: number; tp_pips: number; rr: number;
  tp_basis: string;
};

const LEVEL_STYLE = {
  good: { color: "text-up", bg: "bg-up", label: "良い" },
  fair: { color: "text-amber-400", bg: "bg-amber-400", label: "普通" },
  poor: { color: "text-down", bg: "bg-down", label: "悪い" },
} as const;

const IND_TOGGLES = [
  { key: "showMA", label: "EMA" },
  { key: "showBB", label: "BB" },
  { key: "showMACD", label: "MACD" },
  { key: "showRSI", label: "RSI" },
] as const;

type Ticker = { symbol: string; bid: number; ask: number; spread_pips: number; status: string };

export default function Home() {
  const [s] = useSettings();
  const { data: chart, error: chartErr } = usePoll<ChartData>(
    `/api/chart-data?symbol=${s.symbol}&interval=${s.interval}`, s.refreshSec);
  const { data: tick } = usePoll<Ticker>(`/api/ticker?symbol=${s.symbol}`, 5);
  const { data: plan, error: planErr } = usePoll<EntryPlan>(
    `/api/entry-plan?symbol=${s.symbol}&interval=${s.interval}`, s.refreshSec);


  const digits = s.symbol.endsWith("_JPY") ? 3 : 5;
  const lines: PriceLine[] = plan
    ? [
        { price: plan.sl, color: "rgba(236, 72, 153, 0.7)", title: "" },
        { price: plan.tp, color: "rgba(6, 182, 212, 0.7)", title: "" },
      ]
    : [];

  return (
    <div className="space-y-3">
      <EventBanner />
      {/* ヘッダー: 価格 + 時間足 */}
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-2">
        <div>
          <div className="flex items-baseline gap-2">
            <select value={s.symbol}
              onChange={(e) => saveSettings({ ...s, symbol: e.target.value })}
              className="rounded-lg border border-line bg-panel2 px-2 py-1 text-base font-bold text-white">
              {SYMBOLS.map((x) => <option key={x} value={x}>{x.replace("_", "/")}</option>)}
            </select>
            {tick?.status === "OPEN" && (
              <span className="rounded bg-up/20 px-1.5 py-0.5 text-[10px] font-semibold text-up">LIVE</span>
            )}
            {tick?.status === "CLOSE" && (
              <span className="rounded bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">CLOSED</span>
            )}
          </div>
          <div className="text-2xl font-mono font-semibold text-white">
            {tick ? tick.bid.toFixed(digits) : "—"}
          </div>
        </div>
        <div className="text-xs text-muted">
          <div>SPREAD</div>
          <div className="text-sm text-gray-200 font-mono">{tick?.spread_pips ?? "—"} pips</div>
        </div>
        <div className="ml-auto flex gap-1">
          {INTERVALS.map((tf) => (
            <button key={tf}
              onClick={() => saveSettings({ ...s, interval: tf })}
              className={`rounded-md px-3 py-1.5 text-xs font-medium
                ${s.interval === tf ? "bg-accent text-white" : "bg-panel2 text-muted hover:text-gray-200"}`}>
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* AI予測ミニカード */}
      <div className="card flex items-center gap-4 py-3">
        <span className="text-xs text-muted shrink-0">AI予測 (次の足)</span>
        {plan ? (
          <>
            <span className={`text-sm font-bold shrink-0 ${plan.direction === "UP" ? "text-up" : "text-down"}`}>
              {plan.direction === "UP" ? "▲ 上昇" : "▼ 下落"} {Math.round((plan.direction === "UP" ? plan.prob_up : 1 - plan.prob_up) * 100)}%
            </span>
            <div className="relative h-1.5 flex-1 overflow-hidden rounded bg-panel2">
              <div className={`h-full rounded ${plan.direction === "UP" ? "bg-up" : "bg-down"}`}
                style={{ width: `${Math.round((plan.direction === "UP" ? plan.prob_up : 1 - plan.prob_up) * 100)}%` }} />
              <div className="absolute left-1/2 top-0 h-full w-px bg-line" title="50%" />
            </div>
            <span className="text-xs text-muted shrink-0">信頼度 {plan.confidence}</span>
          </>
        ) : (
          <span className="text-xs text-muted">{planErr ?? "読み込み中..."}</span>
        )}
      </div>

      {/* チャート */}
      <div className="card">
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="mr-1 text-[10px] uppercase tracking-widest text-muted">指標</span>
          {IND_TOGGLES.map((t) => (
            <button key={t.key}
              onClick={() => saveSettings({ ...s, [t.key]: !s[t.key] })}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium
                ${s[t.key] ? "bg-accent/20 text-accent border border-accent/40"
                           : "bg-panel2 text-muted border border-line"}`}>
              {t.label}
            </button>
          ))}
        </div>
        {chartErr && <div className="p-8 text-center text-sm text-down">{chartErr}</div>}
        {!chart && !chartErr && <div className="p-8 text-center text-sm text-muted animate-pulse">チャート読み込み中...</div>}
        {chart && (
          <PriceChart data={chart} showMA={s.showMA} showBB={s.showBB}
            showRSI={s.showRSI} showMACD={s.showMACD} priceLines={lines}
            digits={digits} />
        )}
      </div>
    </div>
  );
}
