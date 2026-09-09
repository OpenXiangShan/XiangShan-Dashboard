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
import {
  getSpecGeomeanName,
  type SpecCategory,
  type SpecVersion,
} from "../config/spec";
import type { NormalizedRun, ReportPayload } from "../types/data";
import { formatDate } from "../services/dataService";
import { isSpecBenchmark } from "./useBenchmarkSelection";

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

function pointStyleFor(
  name: string,
  specVersion: SpecVersion,
): "circle" | "triangle" | "star" | "rect" {
  name = name.replace(/^\d+\./, ""); // remove numeric prefix
  if (name.startsWith("GEOMEAN")) return "star";
  if (isSpecBenchmark(name, specVersion, "int")) return "rect";
  if (isSpecBenchmark(name, specVersion, "fp")) return "triangle";
  return "circle";
}

function computeGeomean(
  payload: ReportPayload,
  metricKey: "ipc" | "score",
  specVersion?: SpecVersion,
  category?: SpecCategory,
): number | null {
  const filtered = Object.entries(payload).filter(([name]) =>
    specVersion && category
      ? isSpecBenchmark(name, specVersion, category)
      : !name.startsWith("legacy"),
  );
  if (!filtered.length) return null;

  const values = filtered
    .map(([, entry]) => entry?.[metricKey] ?? null)
    .filter((v): v is number => typeof v === "number" && v > 0);

  if (!values.length) return null;
  const logSum = values.reduce((acc, v) => acc + Math.log(v), 0);
  return Math.exp(logSum / values.length);
}

function resolveValue(
  payload: ReportPayload | undefined,
  benchmark: string,
  metricKey: "ipc" | "score",
  specVersion: SpecVersion,
): number | null {
  if (!payload) return null;
  if (benchmark === "GEOMEAN") return computeGeomean(payload, metricKey);
  if (benchmark === getSpecGeomeanName(specVersion, "int")) {
    return computeGeomean(payload, metricKey, specVersion, "int");
  }
  if (benchmark === getSpecGeomeanName(specVersion, "fp")) {
    return computeGeomean(payload, metricKey, specVersion, "fp");
  }
  const value = payload[benchmark]?.[metricKey];
  return typeof value === "number" ? value : null;
}

export function renderMetricChart(args: {
  canvas: HTMLCanvasElement;
  chart: Chart | null;
  tab: ChartConfig;
  runs: NormalizedRun[];
  selectedBenchmarks: string[];
  runDataByHash: Record<string, ReportPayload>;
  geomeanMissing: Record<number, Record<string, string[]>>;
  specVersion: SpecVersion;
  t: (key: string) => string;
}): Chart | null {
  const {
    canvas,
    chart,
    tab,
    runs,
    selectedBenchmarks,
    runDataByHash,
    geomeanMissing,
    specVersion,
    t,
  } = args;

  if (chart) {
    chart.destroy();
  }

  if (!runs.length || !selectedBenchmarks.length) {
    return null;
  }

  const labels = runs.map((run) =>
    tab.axisMode === "date" ? formatDate(run.dateMs) : run.runId,
  );
  const isSinglePoint = labels.length === 1;
  const metricLabel = tab.metricKey === "ipc" ? "IPC" : "Score";

  const datasets: ChartDataset<"line">[] = selectedBenchmarks.map(
    (name, idx) => {
      const color = colorForIndex(idx);
      const isGeomean = name.startsWith("GEOMEAN");
      return {
        label: name,
        data: runs.map((run) =>
          resolveValue(
            runDataByHash[run.hash],
            name,
            tab.metricKey,
            specVersion,
          ),
        ),
        borderColor: color,
        backgroundColor: color,
        tension: 0,
        spanGaps: true,
        pointRadius: isGeomean ? 6 : 3,
        pointHoverRadius: isGeomean ? 9 : 5,
        borderWidth: isGeomean ? 4 : 2,
        pointStyle: pointStyleFor(name, specVersion),
        _baseColor: color,
        _baseBorderWidth: isGeomean ? 4 : 2,
        _basePointRadius: isGeomean ? 6 : 3,
        _basePointHoverRadius: isGeomean ? 9 : 5,
      };
    },
  );

  return new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      interaction: { mode: "nearest", intersect: false },
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
            const c = legend.chart as Chart & {
              _highlightedDatasetIndex?: number | null;
            };
            const current = c._highlightedDatasetIndex ?? null;
            c._highlightedDatasetIndex =
              current === targetIndex ? null : targetIndex;
            applyLegendHighlight(c, c._highlightedDatasetIndex);
          },
        },
        tooltip: {
          callbacks: {
            title: (items) => runs[items[0].dataIndex]?.title || "",
            label: (item) =>
              `${item.dataset.label}: ${metricLabel} ${item.formattedValue}`,
            afterLabel: (item) => {
              const run = runs[item.dataIndex];
              if (!run) return [];
              const lines = [
                `${t("commit")}: ${run.hash}`,
                `${t("runId")}: ${run.runId}`,
                `${t("date")}: ${formatDate(run.dateMs)}`,
              ];

              if (run.coverage) {
                lines.push(`${t("coverage")}: ${run.coverage}`);
              }

              if (run.note) {
                lines.push(`${t("note")}: ${run.note}`);
              }

              const geomeanName = item.dataset?.label;
              const missing =
                geomeanMissing?.[item.dataIndex]?.[geomeanName ?? ""];
              if (Array.isArray(missing) && missing.length) {
                lines.push(
                  t("geomeanMissingWarning").replace("{0}", missing.join(", ")),
                );
              }
              return lines;
            },
          },
        },
      },
    },
  });
}
