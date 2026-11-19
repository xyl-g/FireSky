<script lang="ts">
  import uPlot from "uplot";
  import "uplot/dist/uPlot.min.css";

  // uPlot 薄封装（开源引入计划 P1-1）：onMount 建实例、数据/尺寸/主题变化重建、
  // onDestroy 销毁。canvas 不能消费 CSS 变量，颜色统一经 getComputedStyle 解析；
  // 主题切换经 documentElement data-theme MutationObserver 触发重建。
  type ChartData = [number[], ...(number | null)[][]];

  let {
    ariaLabel, height = 210, data, series, xRange, yRange, yTicks, formatX, formatY,
    selection = null, onSelect = () => {}, dataSegments = null, dataPoints = null,
  }: {
    ariaLabel: string; height?: number;
    data: ChartData;
    series: { label: string; color: string; dash?: string; width?: number; pointSize?: number }[];
    xRange: [number, number]; yRange: [number, number];
    yTicks: number[];
    formatX: (value: number) => string; formatY: (value: number) => string;
    selection?: number | null; onSelect?: (index: number) => void;
    // 缺测断线计数供回归用例断言（canvas 无法用 DOM 结构计数）。
    dataSegments?: string | null; dataPoints?: string | null;
  } = $props();

  let host: HTMLDivElement;
  let width = $state(0);
  let themeTick = $state(0);
  let chart: uPlot | null = null;
  const selectionRef: { time: number | null } = { time: null };

  const cssValue = (name: string, fallback: string): string => {
    const value = getComputedStyle(host).getPropertyValue(name).trim();
    return value || fallback;
  };
  // 颜色可写 var(--x)，从 host 沿继承链解析（--trend-* 定义在祖先 section 上）。
  const resolveColor = (value: string): string => {
    const match = /^var\(\s*(--[\w-]+)\s*\)$/.exec(value.trim());
    if (!match) return value;
    return cssValue(match[1], "#808080");
  };

  $effect(() => {
    const observer = new MutationObserver(() => { themeTick += 1; });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  });

  $effect(() => {
    width; themeTick; data; series; xRange; yRange; yTicks;
    if (!host || width <= 0 || data[0].length === 0) return;
    const ink = cssValue("--fs-ink2", "#68686e");
    const border = cssValue("--fs-border", "rgba(24, 24, 28, 0.11)");
    const family = getComputedStyle(host).fontFamily;
    const labelFont = `10px ${family}`;
    const options: uPlot.Options = {
      width, height,
      padding: [26, 14, 24, 38],
      legend: { show: false },
      cursor: { x: true, y: false, points: { show: false } },
      scales: {
        x: { time: false, range: [xRange[0], xRange[1]] },
        y: { range: [yRange[0], yRange[1]] },
      },
      series: [
        { label: "时间" },
        ...series.map((item) => ({
          label: item.label,
          stroke: resolveColor(item.color),
          width: item.width ?? 1.8,
          dash: item.dash ? item.dash.split(/\s+/).map(Number) : [],
          points: { show: true, size: item.pointSize ?? 4.5 },
          spanGaps: false,
        })),
      ],
      axes: [
        {
          grid: { show: false }, ticks: { show: false },
          splits: (_u: uPlot, _axisIdx: number, scaleMin: number, scaleMax: number) => [scaleMin, scaleMax],
          values: (u: uPlot, splits: number[]) => splits.map((value) => formatX(value)),
          stroke: ink, font: labelFont,
        },
        {
          grid: { stroke: border, width: 1 }, ticks: { show: false },
          splits: yTicks,
          values: (u: uPlot, splits: number[]) => splits.map((value) => formatY(value)),
          stroke: ink, font: labelFont,
        },
      ],
      hooks: {
        draw: [(u: uPlot) => {
          const time = selectionRef.time;
          if (time === null) return;
          const px = u.valToPos(time, "x", true);
          const { left, top, width: plotWidth, height: plotHeight } = u.bbox;
          if (px < left - .5 || px > left + plotWidth + .5) return;
          const ctx = u.ctx;
          ctx.save();
          ctx.strokeStyle = ink;
          ctx.globalAlpha = .5;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 4]);
          ctx.beginPath();
          ctx.moveTo(Math.round(px) + .5, top);
          ctx.lineTo(Math.round(px) + .5, top + plotHeight);
          ctx.stroke();
          ctx.restore();
        }],
      },
    };
    chart?.destroy();
    chart = new uPlot(options, data, host);
    const canvas = chart.root.querySelector("canvas");
    canvas?.setAttribute("role", "presentation");
    canvas?.setAttribute("aria-hidden", "true");
    const onClick = () => {
      if (chart?.cursor.idx != null) onSelect(chart.cursor.idx);
    };
    host.addEventListener("click", onClick);
    return () => {
      host.removeEventListener("click", onClick);
      chart?.destroy();
      chart = null;
    };
  });

  $effect(() => {
    // 选中时刻只换十字线不重建实例；draw 钩子从 selectionRef 取最新值。
    selectionRef.time = selection;
    chart?.redraw();
  });
</script>

<div role="img" aria-label={ariaLabel} bind:this={host} bind:clientWidth={width}
     data-segments={dataSegments} data-points={dataPoints} style:height="{height}px">
</div>

<style>
  div { position: relative; min-width: 0; }
</style>
