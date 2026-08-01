import type { Metadata, Viewport } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "FX Predict — AI分析ターミナル",
  description: "自分専用のFX情報分析ツール",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0b0e14",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <div className="flex min-h-screen flex-col md:flex-row">
          <Sidebar />
          <main className="flex-1 p-3 md:p-6 pb-20 md:pb-6 max-w-[1600px]">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
