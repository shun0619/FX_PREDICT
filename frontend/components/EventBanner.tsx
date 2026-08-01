"use client";

import { usePoll, useSettings } from "@/lib/api";

export type EcoEvent = {
  title: string; country: string; impact: string; time: number; in_window: boolean;
};
export type Calendar = { events: EcoEvent[]; warning: boolean };

export function fmtTime(epoch: number) {
  return new Date(epoch * 1000).toLocaleString("ja-JP", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** 高重要度指標が警戒ゾーン(2時間前〜24時間後)にあるとき警告バナーを出す */
export default function EventBanner() {
  const [s] = useSettings();
  const { data } = usePoll<Calendar>(`/api/calendar?symbol=${s.symbol}`, 600);
  const warn = data?.events?.filter((e) => e.in_window) ?? [];
  if (!warn.length) return null;
  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm">
      <div className="font-semibold text-amber-400">⚠ 重要経済指標に注意</div>
      <div className="mt-1 space-y-0.5 text-xs text-amber-200/90">
        {warn.slice(0, 3).map((e, i) => (
          <div key={i}>{fmtTime(e.time)} — {e.country} {e.title}</div>
        ))}
      </div>
      <div className="mt-1 text-[10px] text-amber-200/60">
        指標発表の前後は値動きが乱れ、AI予測は機能しません。この時間帯の予測は参考にしないでください。
      </div>
    </div>
  );
}
