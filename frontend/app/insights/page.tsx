"use client";

import { useState } from "react";
import EventBanner, { Calendar, fmtTime } from "@/components/EventBanner";
import { useSettings, usePoll, postJSON } from "@/lib/api";

type Basis = { feature: string; label: string; contribution: number };
type Predict = {
  prob_up: number; prob_down: number; direction: string; confidence: number;
  bar_time: string; close: number; basis: Basis[];
  model: { trained_at: string; wf_mean_accuracy: number | null; wf_mean_auc: number | null; baseline_accuracy: number | null };
};
type History = {
  items: { bar_time: string; prob_up: number; direction: string; actual: string | null;
           correct: number | null; is_live: boolean }[];
  resolved_accuracy: number | null; resolved_count: number;
  backfilled_excluded: number;
};
type ScoreStats = {
  buckets: { key: string; label: string; n: number; accuracy: number | null }[];
  unscored_resolved: number;
};

/** "2026-07-28 14:00:00+00:00" (UTC) -> 端末のタイムゾーン(日本時間)で表示 */
function fmtBar(t: string) {
  return new Date(t.replace(" ", "T")).toLocaleString("ja-JP", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function Gauge({ value }: { value: number }) {
  const r = 54, c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 140 140" className="w-36 h-36">
      <circle cx="70" cy="70" r={r} fill="none" stroke="#1a1f2b" strokeWidth="10" />
      <circle cx="70" cy="70" r={r} fill="none" stroke="#3b82f6" strokeWidth="10"
        strokeLinecap="round" strokeDasharray={`${(value / 100) * c} ${c}`}
        transform="rotate(-90 70 70)" />
      <text x="70" y="66" textAnchor="middle" fill="#fff" fontSize="28" fontWeight="700">{Math.round(value)}</text>
      <text x="70" y="88" textAnchor="middle" fill="#8b93a7" fontSize="10">信頼度スコア</text>
    </svg>
  );
}

export default function Insights() {
  const [s] = useSettings();
  const [training, setTraining] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const { data: pred, error } = usePoll<Predict>(
    `/api/predict?symbol=${s.symbol}&interval=${s.interval}`, s.refreshSec);
  const { data: hist } = usePoll<History>(
    `/api/history?symbol=${s.symbol}&interval=${s.interval}&limit=60`, 30);
  const { data: cal } = usePoll<Calendar>(`/api/calendar?symbol=${s.symbol}`, 600);
  const { data: stats } = usePoll<ScoreStats>(
    `/api/score-stats?symbol=${s.symbol}&interval=${s.interval}`, 60);

  const needTrain = error?.includes("モデル未学習");

  const train = async () => {
    setTraining(true); setTrainMsg("学習中... (データ取得を含め数分かかることがあります)");
    try {
      const m = await postJSON<any>(`/api/train?symbol=${s.symbol}&interval=${s.interval}`);
      setTrainMsg(`学習完了: WF精度 ${(m.wf_mean_accuracy * 100).toFixed(1)}% / ベースライン ${(m.baseline_accuracy * 100).toFixed(1)}%`);
    } catch (e: any) {
      setTrainMsg(`学習失敗: ${e.message}`);
    } finally {
      setTraining(false);
    }
  };

  const up = pred ? Math.round(pred.prob_up * 100) : 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">AI予測 — {s.symbol.replace("_", "/")} {s.interval}</h1>
        <button onClick={train} disabled={training}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {training ? "学習中..." : "モデル再学習"}
        </button>
      </div>
      <EventBanner />
      {trainMsg && <div className="card py-2 text-xs text-muted">{trainMsg}</div>}
      {needTrain && !training && (
        <div className="card border-accent/40 text-sm">
          モデルが未学習です。「モデル再学習」を押して学習してください。
        </div>
      )}
      {error && !needTrain && <div className="card text-sm text-down">{error}</div>}

      {/* ローディング */}
      {!pred && !error && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="card h-32 animate-pulse">
                <div className="h-3 w-20 rounded bg-panel2" />
                <div className="mt-4 h-8 w-16 rounded bg-panel2" />
              </div>
            ))}
          </div>
          <div className="card flex items-center justify-center gap-3 py-10 text-sm text-muted">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
            AI予測を計算中...
          </div>
        </div>
      )}

      {pred && (
        <>
          {/* 確率カード */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="card">
              <div className="card-title">上昇確率</div>
              <div className="text-3xl font-bold text-up">{up}%</div>
              <div className="mt-2 h-1.5 rounded bg-panel2"><div className="h-full rounded bg-up" style={{ width: `${up}%` }} /></div>
            </div>
            <div className="card">
              <div className="card-title">下落確率</div>
              <div className="text-3xl font-bold text-down">{100 - up}%</div>
              <div className="mt-2 h-1.5 rounded bg-panel2"><div className="h-full rounded bg-down" style={{ width: `${100 - up}%` }} /></div>
            </div>
            <div className="card flex items-center justify-center col-span-2 md:col-span-1">
              <Gauge value={pred.confidence} />
            </div>
            <div className="card col-span-2 md:col-span-1">
              <div className="card-title">モデル成績 (検証)</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-muted">WF精度</span>
                  <span className="font-mono">{pred.model.wf_mean_accuracy != null ? (pred.model.wf_mean_accuracy * 100).toFixed(1) + "%" : "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted">AUC</span>
                  <span className="font-mono">{pred.model.wf_mean_auc ?? "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted">ベースライン</span>
                  <span className="font-mono">{pred.model.baseline_accuracy != null ? (pred.model.baseline_accuracy * 100).toFixed(1) + "%" : "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted">実測正解率</span>
                  <span className="font-mono">{hist?.resolved_accuracy != null ? `${hist.resolved_accuracy}% (${hist.resolved_count}件)` : "集計中"}</span></div>
                {!!hist?.backfilled_excluded && (
                  <div className="text-[9px] leading-relaxed text-muted">
                    ※停止中を埋めた{hist.backfilled_excluded}件は集計外
                    (学習済み期間の再計算で実力より高く出るため)
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* 予測根拠 */}
          <div className="grid md:grid-cols-2 gap-3">
            <div className="card">
              <div className="card-title">予測根拠 (特徴量寄与)</div>
              <div className="space-y-3 mt-3">
                {pred.basis.map((b) => (
                  <div key={b.feature}>
                    <div className="flex justify-between text-xs mb-1">
                      <span>{b.label}</span>
                      <span className={`font-mono ${b.contribution >= 0 ? "text-up" : "text-down"}`}>
                        {b.contribution >= 0 ? "+" : ""}{b.contribution}%
                      </span>
                    </div>
                    <div className="h-1.5 rounded bg-panel2">
                      <div className={`h-full rounded ${b.contribution >= 0 ? "bg-up" : "bg-down"}`}
                        style={{ width: `${Math.min(100, Math.abs(b.contribution) * 2)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-4 text-[10px] text-muted">正=上昇方向への寄与 / 負=下落方向への寄与 (SHAP近似)</p>
            </div>

            {/* 予測履歴 */}
            <div className="card">
              <div className="card-title">予測履歴 (直近)</div>
              <div className="max-h-80 overflow-y-auto divide-y divide-line">
                {hist?.items.map((h) => (
                  <div key={h.bar_time} className="flex items-center gap-2 py-2 text-xs">
                    <span className={h.correct == null ? "text-muted" : h.correct ? "text-up" : "text-down"}>
                      {h.correct == null ? "⏳" : h.correct ? "✓" : "✗"}
                    </span>
                    <span className="text-muted font-mono">{fmtBar(h.bar_time)}</span>
                    <span className={h.direction === "UP" ? "text-up" : "text-down"}>{h.direction}</span>
                    <span className="ml-auto font-mono">{Math.round(h.prob_up * 100)}%</span>
                  </div>
                ))}
                {!hist?.items.length && <div className="py-6 text-center text-xs text-muted">履歴なし</div>}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Entry Scoreの答え合わせ */}
      {stats && (
        <div className="card">
          <div className="card-title">Entry Scoreの答え合わせ (スコア帯別の実測的中率)</div>
          <div className="mt-2 space-y-3">
            {stats.buckets.map((b) => {
              const col = b.key === "good" ? "text-up" : b.key === "fair" ? "text-amber-400" : "text-down";
              const bar = b.key === "good" ? "bg-up" : b.key === "fair" ? "bg-amber-400" : "bg-down";
              return (
                <div key={b.key} className="flex items-center gap-3 text-xs">
                  <span className={`w-32 shrink-0 ${col}`}>{b.label}</span>
                  <div className="h-2 flex-1 rounded bg-panel2">
                    {b.accuracy != null && (
                      <div className={`h-full rounded ${bar}`}
                        style={{ width: `${Math.min(100, b.accuracy)}%` }} />
                    )}
                  </div>
                  <span className="w-24 text-right font-mono">
                    {b.accuracy != null ? `${b.accuracy}%` : "—"}
                    <span className="ml-1 text-[9px] text-muted">({b.n}件)</span>
                  </span>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-[10px] leading-relaxed text-muted">
            スコアが機能していれば、上の段ほど的中率が高くなるはずです。
            各帯30件以上たまってから判断してください。差が出ない場合はスコアの配点を見直します。
            {stats.unscored_resolved > 0 && ` (スコア導入前の記録 ${stats.unscored_resolved}件は集計対象外)`}
          </p>
        </div>
      )}

      {/* 経済指標カレンダー */}
      {!!cal?.events?.length && (
        <div className="card">
          <div className="card-title">今週の経済指標 ({s.symbol.replace("_", "/")}関連)</div>
          <div className="divide-y divide-line">
            {cal.events.map((e, i) => (
              <div key={i} className="flex items-center gap-3 py-2 text-xs">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold
                  ${e.impact === "High" ? "bg-down/20 text-down" : "bg-amber-500/20 text-amber-400"}`}>
                  {e.impact === "High" ? "高" : "中"}
                </span>
                <span className="w-24 font-mono text-muted">{fmtTime(e.time)}</span>
                <span className="text-muted">{e.country}</span>
                <span className="truncate">{e.title}</span>
                {e.in_window && <span className="ml-auto text-amber-400">⚠ 警戒中</span>}
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-muted">
            高重要度指標の2時間前〜24時間後は警戒表示。時刻はこの端末のタイムゾーン。
          </p>
        </div>
      )}
    </div>
  );
}
