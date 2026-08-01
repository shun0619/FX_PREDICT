"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, Time } from "lightweight-charts";
import { useSettings, getJSON } from "@/lib/api";

type BT = {
  bars: number; n_trades: number; win_rate: number; profit_factor: number | null;
  expectancy_pips: number; max_drawdown_pct: number; total_pnl_jpy: number;
  initial_capital: number;
  equity_curve: { time: number; equity: number }[];
  monthly: { month: string; return_pct: number }[];
  recent_trades: { time: string; type: string; entry: number; pnl_pips: number; pnl_jpy: number }[];
  best_month: number; worst_month: number; avg_month: number;
  period: { from: string; to: string };
};

/** UTC文字列 -> 日本時間表示 */
function fmtTime(t: string) {
  return new Date(t.replace(" ", "T")).toLocaleString("ja-JP", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function EquityChart({ curve }: { curve: BT["equity_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !curve.length) return;
    const chart = createChart(ref.current, {
      height: 280,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b93a7" },
      grid: { vertLines: { color: "#1a1f2b" }, horzLines: { color: "#1a1f2b" } },
      timeScale: { timeVisible: false, borderColor: "#232a38" },
      rightPriceScale: { borderColor: "#232a38" },
    });
    const area = chart.addAreaSeries({
      lineColor: "#3b82f6", topColor: "rgba(59,130,246,0.3)", bottomColor: "rgba(59,130,246,0.02)",
      lineWidth: 2,
    });
    area.setData(curve.map((p) => ({ time: p.time as Time, value: p.equity })));
    chart.timeScale().fitContent();
    const resize = () => ref.current && chart.applyOptions({ width: ref.current.clientWidth });
    resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); };
  }, [curve]);
  return <div ref={ref} className="w-full" />;
}

export default function Backtest() {
  const [s, save] = useSettings();
  const [bt, setBt] = useState<BT | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      setBt(await getJSON<BT>(
        `/api/backtest?symbol=${s.symbol}&interval=${s.interval}&threshold=${s.btThreshold}&spread_pips=${s.btSpread}`));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold text-white">バックテスト — {s.symbol.replace("_", "/")} {s.interval}</h1>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <label className="text-muted">閾値</label>
          <select value={s.btThreshold}
            onChange={(e) => save({ ...s, btThreshold: Number(e.target.value) })}
            className="rounded bg-panel2 border border-line px-2 py-1.5">
            {[0.52, 0.55, 0.6, 0.65, 0.7].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <label className="text-muted">スプレッド</label>
          <select value={s.btSpread}
            onChange={(e) => save({ ...s, btSpread: Number(e.target.value) })}
            className="rounded bg-panel2 border border-line px-2 py-1.5">
            {[0.2, 0.3, 0.5, 1.0].map((v) => <option key={v} value={v}>{v} pips</option>)}
          </select>
          <button onClick={run} disabled={loading}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50">
            {loading ? "実行中..." : "実行"}
          </button>
        </div>
      </div>

      {err && <div className="card text-sm text-down">{err}</div>}
      {!bt && !err && !loading && (
        <div className="card py-10 text-center text-sm text-muted">
          「実行」を押すと、学習に使っていない区間での成績をシミュレーションします<br />
          <span className="text-[10px]">
            ※ウォークフォワード検証(未学習データに対する予測)を使用。スプレッドコスト込み
          </span>
        </div>
      )}
      {loading && <div className="card py-10 text-center text-sm text-muted animate-pulse">計算中...</div>}

      {bt && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="card"><div className="card-title">勝率</div>
              <div className="text-2xl font-bold text-white">{bt.win_rate}%</div>
              <div className="text-[10px] text-muted">{bt.n_trades} トレード</div></div>
            <div className="card"><div className="card-title">期待値</div>
              <div className={`text-2xl font-bold ${bt.expectancy_pips >= 0 ? "text-up" : "text-down"}`}>
                {bt.expectancy_pips > 0 ? "+" : ""}{bt.expectancy_pips}</div>
              <div className="text-[10px] text-muted">pips / トレード</div></div>
            <div className="card"><div className="card-title">PF</div>
              <div className="text-2xl font-bold text-white">{bt.profit_factor ?? "∞"}</div>
              <div className="text-[10px] text-muted">プロフィットファクター</div></div>
            <div className="card"><div className="card-title">最大DD</div>
              <div className="text-2xl font-bold text-down">{bt.max_drawdown_pct}%</div>
              <div className="text-[10px] text-muted">最大ドローダウン</div></div>
            <div className="card col-span-2 md:col-span-1"><div className="card-title">合計損益</div>
              <div className={`text-2xl font-bold ${bt.total_pnl_jpy >= 0 ? "text-up" : "text-down"}`}>
                {bt.total_pnl_jpy >= 0 ? "+" : ""}{bt.total_pnl_jpy.toLocaleString()}</div>
              <div className="text-[10px] text-muted">初期資金 {bt.initial_capital.toLocaleString()} / 1万通貨</div></div>
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <div className="card md:col-span-2">
              <div className="card-title">損益曲線</div>
              <EquityChart curve={bt.equity_curve} />
            </div>
            <div className="card">
              <div className="card-title">月別成績</div>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {bt.monthly.map((m) => (
                  <div key={m.month} className="flex items-center gap-2 text-xs">
                    <span className="w-16 text-muted font-mono">{m.month}</span>
                    <div className="flex-1 h-2 rounded bg-panel2 relative overflow-hidden">
                      <div className={`absolute top-0 h-full ${m.return_pct >= 0 ? "bg-up left-1/2" : "bg-down right-1/2"}`}
                        style={{ width: `${Math.min(50, Math.abs(m.return_pct) * 5)}%` }} />
                    </div>
                    <span className={`w-14 text-right font-mono ${m.return_pct >= 0 ? "text-up" : "text-down"}`}>
                      {m.return_pct > 0 ? "+" : ""}{m.return_pct}%
                    </span>
                  </div>
                ))}
              </div>
              <div className="mt-3 border-t border-line pt-2 space-y-1 text-xs">
                <div className="flex justify-between"><span className="text-muted">最高月</span><span className="text-up">+{bt.best_month}%</span></div>
                <div className="flex justify-between"><span className="text-muted">最低月</span><span className="text-down">{bt.worst_month}%</span></div>
                <div className="flex justify-between"><span className="text-muted">平均月次</span><span>{bt.avg_month}%</span></div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-title">直近トレード</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-muted border-b border-line">
                  <th className="py-2 text-left">日時</th><th className="text-left">方向</th>
                  <th className="text-right">エントリー</th><th className="text-right">pips</th><th className="text-right">損益</th>
                </tr></thead>
                <tbody className="divide-y divide-line font-mono">
                  {bt.recent_trades.map((t, i) => (
                    <tr key={i}>
                      <td className="py-2 text-muted">{fmtTime(t.time)}</td>
                      <td><span className={`rounded px-1.5 py-0.5 text-[10px] font-sans font-semibold
                        ${t.type === "LONG" ? "bg-up/20 text-up" : "bg-down/20 text-down"}`}>{t.type}</span></td>
                      <td className="text-right">{t.entry}</td>
                      <td className={`text-right ${t.pnl_pips >= 0 ? "text-up" : "text-down"}`}>
                        {t.pnl_pips > 0 ? "+" : ""}{t.pnl_pips}</td>
                      <td className={`text-right ${t.pnl_jpy >= 0 ? "text-up" : "text-down"}`}>
                        {t.pnl_jpy > 0 ? "+" : ""}{t.pnl_jpy.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[10px] leading-relaxed text-muted">
              期間: {bt.period.from.slice(0, 10)} 〜 {bt.period.to.slice(0, 10)} / {bt.bars.toLocaleString()}本 /
              戦略: 確率{s.btThreshold}以上でロング・{(1 - s.btThreshold).toFixed(2)}以下でショート、次足決済
              <br />
              <span className="text-up">✓ 学習に使っていないデータでの成績です</span>
              (ウォークフォワード検証。実運用に近い数字ですが、スリッページ・スワップは未考慮)
            </p>
          </div>
        </>
      )}
    </div>
  );
}
