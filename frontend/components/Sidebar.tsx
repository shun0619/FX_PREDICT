"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "チャート", icon: "📊" },
  { href: "/insights", label: "AI予測", icon: "🧠" },
  { href: "/trade", label: "取引", icon: "💱" },
  { href: "/backtest", label: "バックテスト", icon: "📈" },
  { href: "/settings", label: "設定", icon: "⚙️" },
];

export default function Sidebar() {
  const path = usePathname();
  return (
    <>
      {/* PC: 左サイドバー */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col border-r border-line bg-panel min-h-screen p-4 gap-1">
        <div className="mb-6 px-2">
          <div className="text-lg font-bold text-white">FX Predict</div>
          <div className="text-[11px] tracking-widest text-muted">AI ANALYSIS TERMINAL</div>
        </div>
        {NAV.map((n) => (
          <Link key={n.href} href={n.href}
            className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition
              ${path === n.href ? "bg-accent text-white" : "text-gray-300 hover:bg-panel2"}`}>
            <span>{n.icon}</span>{n.label}
          </Link>
        ))}
      </aside>

      {/* スマホ: 下タブ */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-50 flex border-t border-line bg-panel">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href}
            className={`flex-1 py-2.5 text-center text-[11px] ${path === n.href ? "text-accent" : "text-muted"}`}>
            <div className="text-base">{n.icon}</div>{n.label}
          </Link>
        ))}
      </nav>
    </>
  );
}
