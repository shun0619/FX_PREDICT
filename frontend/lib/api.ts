"use client";

import { useEffect, useState } from "react";

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: body != null ? { "Content-Type": "application/json" } : undefined,
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

/* ---------- 設定 (localStorage) ---------- */

export type Settings = {
  symbol: string;
  interval: string;
  refreshSec: number;
  showMA: boolean;
  showBB: boolean;
  showRSI: boolean;
  showMACD: boolean;
  btThreshold: number;
  btSpread: number;
};

export const DEFAULT_SETTINGS: Settings = {
  symbol: "USD_JPY",
  interval: "15m",
  refreshSec: 15,
  showMA: true,
  showBB: false,
  showRSI: true,
  showMACD: true,
  btThreshold: 0.55,
  btSpread: 0.2,
};

const KEY = "fxpredict.settings";

export function loadSettings(): Settings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: Settings) {
  localStorage.setItem(KEY, JSON.stringify(s));
  window.dispatchEvent(new Event("fx-settings"));
}

export function useSettings(): [Settings, (s: Settings) => void] {
  const [s, setS] = useState<Settings>(DEFAULT_SETTINGS);
  useEffect(() => {
    setS(loadSettings());
    const h = () => setS(loadSettings());
    window.addEventListener("fx-settings", h);
    return () => window.removeEventListener("fx-settings", h);
  }, []);
  return [s, saveSettings];
}

/* ---------- ポーリング ---------- */

export function usePoll<T>(path: string | null, intervalSec: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const tick = async () => {
      try {
        const d = await getJSON<T>(path);
        if (alive) { setData(d); setError(null); }
      } catch (e: any) {
        if (alive) setError(e.message);
      }
    };
    tick();
    const id = setInterval(tick, Math.max(3, intervalSec) * 1000);
    return () => { alive = false; clearInterval(id); };
  }, [path, intervalSec]);
  return { data, error };
}

// 主要3ペアに限定 (バックエンドの gmo.SYMBOLS と一致させる)
export const SYMBOLS = ["USD_JPY", "EUR_USD", "GBP_USD"];
// 1分足・5分足はエッジがほぼ無く値動きも荒いため対象外
export const INTERVALS = ["15m", "1h", "4h"];
