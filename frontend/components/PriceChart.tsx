"use client";

import { useEffect, useRef } from "react";
import {
  createChart, IChartApi, ISeriesApi, ColorType, Time, LineStyle,
} from "lightweight-charts";

export type ChartData = {
  time: number[]; open: number[]; high: number[]; low: number[]; close: number[];
  ema20: (number | null)[]; ema50: (number | null)[];
  bb_upper: (number | null)[]; bb_lower: (number | null)[];
  rsi14: (number | null)[];
  macd_hist: (number | null)[];
};

const BASE_OPTS = {
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b93a7" },
  grid: { vertLines: { color: "#1a1f2b" }, horzLines: { color: "#1a1f2b" } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#232a38" },
  rightPriceScale: { borderColor: "#232a38" },
  crosshair: { horzLine: { labelBackgroundColor: "#3b82f6" }, vertLine: { labelBackgroundColor: "#3b82f6" } },
};

function line(xs: number[], ys: (number | null)[]) {
  return xs.map((t, i) => ({ time: t as Time, value: ys[i] }))
    .filter((p): p is { time: Time; value: number } => p.value != null);
}

export type PriceLine = { price: number; color: string; title: string };

export default function PriceChart({ data, showMA, showBB, showRSI, showMACD, priceLines, digits }: {
  data: ChartData; showMA: boolean; showBB: boolean; showRSI: boolean; showMACD: boolean;
  priceLines?: PriceLine[];
  /** 価格の小数桁数。JPYペアは3、ドルストレートは5 */
  digits?: number;
}) {
  const d = digits ?? 5;
  const priceRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const charts = useRef<IChartApi[]>([]);
  const series = useRef<Record<string, ISeriesApi<any> | undefined>>({});
  const firstFit = useRef(true);
  const lineRefs = useRef<any[]>([]);

  // チャート生成: サブ枠の構成が変わったら安全に作り直す
  useEffect(() => {
    if (!priceRef.current) return;
    const created: IChartApi[] = [];
    const s: Record<string, ISeriesApi<any>> = {};

    // 通貨ペアに応じた小数桁数。既定の2桁だとEUR/USDが「1.15」としか出ない
    const priceFormat = {
      type: "price" as const,
      precision: d,
      minMove: Number(`1e-${d}`),
    };
    const price = createChart(priceRef.current, { ...BASE_OPTS, height: 420 });
    created.push(price);
    s.candle = price.addCandlestickSeries({
      upColor: "#22c55e", downColor: "#ef4444",
      wickUpColor: "#22c55e", wickDownColor: "#ef4444", borderVisible: false,
      priceFormat,
    });
    s.ema20 = price.addLineSeries({ color: "#60a5fa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat });
    s.ema50 = price.addLineSeries({ color: "#f59e0b", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat });
    s.bbU = price.addLineSeries({ color: "#8b93a7", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, priceFormat });
    s.bbL = price.addLineSeries({ color: "#8b93a7", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, priceFormat });

    if (showMACD && macdRef.current) {
      const macd = createChart(macdRef.current, { ...BASE_OPTS, height: 120 });
      created.push(macd);
      s.macd = macd.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    }
    if (showRSI && rsiRef.current) {
      const rsi = createChart(rsiRef.current, { ...BASE_OPTS, height: 120 });
      created.push(rsi);
      s.rsi = rsi.addLineSeries({ color: "#a78bfa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.rsi.createPriceLine({ price: 70, color: "#8b93a7", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
      s.rsi.createPriceLine({ price: 30, color: "#8b93a7", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    }

    // 全チャートの時間軸を相互同期(幅0などの例外は握りつぶす)
    let lock = false;
    for (const from of created) {
      from.timeScale().subscribeVisibleLogicalRangeChange((r) => {
        if (lock || !r) return;
        lock = true;
        for (const to of created) {
          if (to === from) continue;
          try { to.timeScale().setVisibleLogicalRange(r); } catch {}
        }
        lock = false;
      });
    }

    const resize = () => {
      try {
        const w = priceRef.current?.clientWidth;
        if (!w) return;
        for (const c of created) c.applyOptions({ width: w });
      } catch {}
    };
    resize();
    window.addEventListener("resize", resize);

    charts.current = created;
    series.current = s;
    firstFit.current = true;

    return () => {
      window.removeEventListener("resize", resize);
      for (const c of created) {
        try { c.remove(); } catch {}
      }
      charts.current = [];
      series.current = {};
    };
  }, [showMACD, showRSI, d]);

  // データ反映
  useEffect(() => {
    const s = series.current;
    if (!s.candle || !data?.time?.length) return;
    try {
      s.candle.setData(data.time.map((t, i) => ({
        time: t as Time, open: data.open[i], high: data.high[i],
        low: data.low[i], close: data.close[i],
      })));
      s.ema20?.setData(showMA ? line(data.time, data.ema20) : []);
      s.ema50?.setData(showMA ? line(data.time, data.ema50) : []);
      s.bbU?.setData(showBB ? line(data.time, data.bb_upper) : []);
      s.bbL?.setData(showBB ? line(data.time, data.bb_lower) : []);
      s.macd?.setData(data.time.map((t, i) => ({
        time: t as Time, value: data.macd_hist[i] ?? 0,
        color: (data.macd_hist[i] ?? 0) >= 0 ? "#22c55e" : "#ef4444",
      })));
      s.rsi?.setData(line(data.time, data.rsi14));
      if (firstFit.current && charts.current[0]) {
        charts.current[0].timeScale().fitContent();
        firstFit.current = false;
      }
    } catch {}
  }, [data, showMA, showBB, showMACD, showRSI]);

  // SL/TPなどの水平ライン
  useEffect(() => {
    const candle = series.current.candle;
    if (!candle) return;
    try {
      for (const l of lineRefs.current) candle.removePriceLine(l);
    } catch {}
    lineRefs.current = [];
    if (!priceLines?.length) return;
    try {
      lineRefs.current = priceLines.map((p) => candle.createPriceLine({
        price: p.price, color: p.color, lineWidth: 1,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: p.title,
      }));
    } catch {}
  }, [priceLines, data, showMACD, showRSI]);

  return (
    <div>
      {/* 高さを固定してチャート再生成時のレイアウト崩れ(スクロール飛び)を防ぐ */}
      <div ref={priceRef} className="w-full h-[420px]" />
      {showMACD && (
        <>
          <div className="mt-1 px-1 text-[10px] text-muted">MACD (12, 26, 9)</div>
          <div ref={macdRef} className="w-full h-[120px]" />
        </>
      )}
      {showRSI && (
        <>
          <div className="mt-1 px-1 text-[10px] text-muted">RSI (14)</div>
          <div ref={rsiRef} className="w-full h-[120px]" />
        </>
      )}
    </div>
  );
}
