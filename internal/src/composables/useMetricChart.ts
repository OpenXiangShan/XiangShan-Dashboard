import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Title,
  Tooltip,
  Legend,
  type ChartDataset,
} from "chart.js";
import type { ChartConfig } from "../config/tabs";
import type { NormalizedRun, ReportPayload } from "../types/data";
import { formatDate } from "../services/dataService";

Chart.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Title,
  Tooltip,
  Legend,
);

const COVERAGE_COLORS: Record<string, string> = {
  "0.3c": "hsl(217, 85%, 55%)",
  "1.0c": "hsl(28, 90%, 52%)",
};

function colorForIndex(i: number): string {
  const hue = (i * 47) % 360;
  return `hsl(${hue}, 72%, 52%)`;
}

function withAlpha(color: string, alpha: number): string {
  const hslMatch = color.match(/^hsl\(([^)]+)\)$/);
  if (hslMatch) {
    return `hsla(${hslMatch[1]}, ${alpha})`;
  }
  const hslaMatch = color.match(/^hsla\(([^,]+),([^,]+),([^,]+),[^)]+\)$/);
  if (hslaMatch) {
    return `hsla(${hslaMatch[1]},${hslaMatch[2]},${hslaMatch[3]},${alpha})`;
  }
  return color;
}

type HighlightDataset = ChartDataset<"line"> & {
  _baseColor?: string;
  _baseBorderWidth?: number;
  _basePointRadius?: number;
  _basePointHoverRadius?: number;
};

function applyLegendHighlight(chart: Chart, highlightedIndex: number | null) {
  const datasets = chart.data.datasets as HighlightDataset[];
  datasets.forEach((dataset, idx) => {
    const baseColor = dataset._baseColor ?? String(dataset.borderColor);
    const baseBorderWidth = dataset._baseBorderWidth ?? 2;
    const basePointRadius = dataset._basePointRadius ?? 3;
    const basePointHoverRadius = dataset._basePointHoverRadius ?? 5;

    if (highlightedIndex === null) {
      dataset.borderColor = baseColor;
      dataset.backgroundColor = baseColor;
      dataset.borderWidth = baseBorderWidth;
      dataset.pointRadius = basePointRadius;
      dataset.pointHoverRadius = basePointHoverRadius;
      dataset.hidden = false;
      return;
    }

    const isActive = idx === highlightedIndex;
    const alpha = isActive ? 1 : 0.18;
    dataset.borderColor = withAlpha(baseColor, alpha);
    dataset.backgroundColor = withAlpha(baseColor, alpha);
    dataset.borderWidth = isActive
      ? baseBorderWidth + 1
      : Math.max(1, baseBorderWidth - 1);
    dataset.pointRadius = isActive
      ? basePointRadius + 1
      : Math.max(1, basePointRadius - 1);
    dataset.pointHoverRadius = isActive
      ? basePointHoverRadius + 1
      : Math.max(2, basePointHoverRadius - 2);
    dataset.hidden = false;
  });
  chart.update();
}

export function renderMetricChart(args: {
  canvas: HTMLCanvasElement;
  chart: Chart | null;
  tab: ChartConfig;
  runs: NormalizedRun[];
  selectedBenchmarks: string[];
  runDataById: Record<string, ReportPayload>;
  baseRunId: string;
  targetRunId: string;
  onRunSelect?: (runId: string) => void;
  t: (key: string) => string;
}): Chart | null {
  const {
    canvas,
    chart,
    tab,
    runs,
    selectedBenchmarks,
    runDataById,
    baseRunId,
    targetRunId,
    onRunSelect,
    t,
  } = args;

  if (chart) {
    chart.destroy();
  }
  canvas.style.cursor = "";

  if (!runs.length || !selectedBenchmarks.length) {
    return null;
  }

  const labels = runs.map((run) =>
    tab.axisMode === "date" ? formatDate(run.dateMs) : run.runId,
  );
  const isSinglePoint = labels.length === 1;
  const metricLabel = tab.metricLabel;
  let highlightedIndex: number | null = null;

  const splitCoverage = tab.id === "score";
  const coverages = splitCoverage
    ? [...new Set(runs.map((run) => run.coverage))].sort()
    : [null];
  const datasets: ChartDataset<"line">[] = selectedBenchmarks.flatMap(
    (name, idx) =>
      coverages
        .filter(
          (coverage) =>
            !splitCoverage ||
            runs.some(
              (run) =>
                run.coverage === coverage &&
                runDataById[run.runId]?.[name]?.[tab.metricKey] != null,
            ),
        )
        .map((coverage) => {
          const color =
            (splitCoverage && COVERAGE_COLORS[coverage || ""]) ||
            colorForIndex(idx);
          const isGeomean = name.startsWith("SPEC");
          return {
            label: splitCoverage
              ? `${name} · ${coverage?.replace(/c$/, "cov") || "范围未知"}`
              : name,
            data: runs.map((run) =>
              !splitCoverage || run.coverage === coverage
                ? (runDataById[run.runId]?.[name]?.[tab.metricKey] ?? null)
                : null,
            ),
            borderColor: color,
            backgroundColor: color,
            tension: 0,
            spanGaps: true,
            pointRadius: isGeomean ? 6 : 3,
            pointHoverRadius: isGeomean ? 9 : 5,
            borderWidth: isGeomean ? 4 : 2,
            pointStyle: isGeomean ? "star" : "circle",
            _baseColor: color,
            _baseBorderWidth: isGeomean ? 4 : 2,
            _basePointRadius: isGeomean ? 6 : 3,
            _basePointHoverRadius: isGeomean ? 9 : 5,
          };
        }),
  );

  return new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    plugins:
      tab.id === "score"
        ? [
            {
              id: "comparisonMarkers",
              afterDatasetsDraw(chart) {
                const {
                  ctx,
                  chartArea: { top, bottom, left, right },
                } = chart;
                const markers =
                  baseRunId === targetRunId
                    ? [{ runId: baseRunId, label: "A/B" }]
                    : [
                        { runId: baseRunId, label: "A" },
                        { runId: targetRunId, label: "B" },
                      ];
                ctx.save();
                ctx.font = 'bold 12px "Segoe UI", sans-serif';
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                markers.forEach((marker, markerIndex) => {
                  const index = runs.findIndex(
                    (run) => run.runId === marker.runId,
                  );
                  if (index < 0) return;
                  const x = chart.scales.x.getPixelForValue(index);
                  const width = ctx.measureText(marker.label).width + 16;
                  const labelX = Math.max(
                    left + width / 2,
                    Math.min(right - width / 2, x),
                  );
                  const labelY = top + 12 + markerIndex * 24;
                  ctx.strokeStyle = "#64748b";
                  ctx.lineWidth = 1;
                  ctx.setLineDash([4, 4]);
                  ctx.beginPath();
                  ctx.moveTo(x, top);
                  ctx.lineTo(x, bottom);
                  ctx.stroke();
                  ctx.setLineDash([]);
                  ctx.fillStyle = "#f1f5f9";
                  ctx.fillRect(labelX - width / 2, labelY - 10, width, 20);
                  ctx.strokeRect(labelX - width / 2, labelY - 10, width, 20);
                  ctx.fillStyle = "#1c1f2a";
                  ctx.fillText(marker.label, labelX, labelY);
                });
                ctx.restore();
              },
            },
          ]
        : [],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      interaction: { mode: "nearest", intersect: false },
      onClick: (_event, elements) => {
        const point = elements[0];
        if (point) onRunSelect?.(runs[point.index].runId);
      },
      scales: {
        x: {
          offset: isSinglePoint,
          title: {
            display: true,
            text: tab.axisMode === "date" ? t("dateAxis") : t("runIdAxis"),
          },
          ticks: { maxRotation: 25, minRotation: 25 },
        },
        y: {
          title: { display: true, text: metricLabel },
          beginAtZero: false,
        },
      },
      plugins: {
        legend: {
          labels: { boxWidth: 12, color: "#1c1f2a" },
          onClick: (_event, legendItem, legend) => {
            const targetIndex = legendItem.datasetIndex;
            if (typeof targetIndex !== "number") return;
            highlightedIndex =
              highlightedIndex === targetIndex ? null : targetIndex;
            applyLegendHighlight(legend.chart, highlightedIndex);
          },
        },
        tooltip: {
          callbacks: {
            title(items) {
              const title = `${t("commitTitle")}: ${runs[items[0].dataIndex].title}`;
              const width = Math.max(
                18,
                Math.min(60, Math.floor((this.chart.width - 24) / 12)),
              );
              return title.match(new RegExp(`.{1,${width}}`, "g")) || [];
            },
            label: (item) =>
              `${item.dataset.label}: ${metricLabel} ${item.formattedValue}`,
            afterLabel: (item) => {
              const run = runs[item.dataIndex];
              if (!run) return [];
              const lines = [
                `${t("commit")}: ${run.hash.slice(0, 9)}`,
                `${t("date")}: ${formatDate(run.dateMs)}`,
              ];

              if (run.coverage) {
                lines.push(`${t("coverage")}: ${run.coverage}`);
              }

              return lines;
            },
          },
        },
      },
    },
  });
}
