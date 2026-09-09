<template>
  <div ref="exportRoot" class="comparison-layout">
    <section class="panel-surface panel-shell">
      <div class="panel-header">
        <h2 class="panel-title">
          {{ t("comparisonTitle") }} ({{ coverage }})
          <span class="panel-subtitle"
            >{{ sourceAName }} <strong>vs</strong> {{ sourceBName }}</span
          >
        </h2>
      </div>
      <div v-if="warnings.length" class="comparison-warnings">
        <div v-for="warning in warnings" :key="warning">{{ warning }}</div>
      </div>
      <div v-if="!benchmarkCount" class="empty">
        {{ t("comparisonNoData") }}
      </div>
      <div v-else class="comparison-tables">
        <div v-for="group in tableGroups" :key="group.key" class="table-group">
          <h3>{{ group.title }}</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{{ t("comparisonBenchmark") }}</th>
                  <th>{{ t("comparisonSourceA") }}</th>
                  <th>{{ t("comparisonSourceB") }}</th>
                  <th>{{ t("comparisonDiff") }}</th>
                  <th>{{ t("comparisonDiffPct") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in rowsByClass[group.key]"
                  :key="row.name"
                  :class="{ 'geomean-row': row.name.startsWith('GEOMEAN') }"
                >
                  <td>{{ row.name }}</td>
                  <td>{{ format(row.a, row.name.startsWith("GEOMEAN")) }}</td>
                  <td>{{ format(row.b, row.name.startsWith("GEOMEAN")) }}</td>
                  <td :style="conditionalStyle(row.diff, maxAbsDiff)">
                    {{ format(row.diff, row.name.startsWith("GEOMEAN")) }}
                  </td>
                  <td :style="conditionalStyle(row.diffPct, maxAbsDiffPct)">
                    {{ formatPct(row.diffPct) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { toPng } from "html-to-image";
import {
  getSpecGeomeanName,
  getSpecGroup,
  SPEC_BENCHMARK_GROUPS,
} from "../../config/spec";
import type { SpecCategory, SpecVersion } from "../../config/spec";
import { formatDisplayDate } from "../../services/dataService";
import type { ReportPayload } from "../../types/data";
import type { ComparisonSource } from "../../types/comparison";
import {
  EXPORT_LAYOUT_WIDTH,
  EXPORT_PIXEL_RATIO,
} from "../../constants/export";

interface ComparisonRow {
  name: string;
  a: number | null;
  b: number | null;
  diff: number | null;
  diffPct: number | null;
}

const props = defineProps<{
  t: (key: string) => string;
  sources: ComparisonSource[];
  coverage: string;
  specVersion: SpecVersion;
}>();

const exportRoot = ref<HTMLElement | null>(null);

const sources = computed(() => props.sources);
const sourceAName = computed(() => sourceName(sources.value[0]));
const sourceBName = computed(() => sourceName(sources.value[1]));
const tableGroups = computed(() =>
  (["int", "fp"] as const).map((key) => ({
    key,
    title: getSpecGroup(props.specVersion, key).name,
  })),
);
const rowsByClass = computed<Record<SpecCategory, ComparisonRow[]>>(() => ({
  int: buildRows("int"),
  fp: buildRows("fp"),
}));
const allRows = computed(() => [
  ...rowsByClass.value.int,
  ...rowsByClass.value.fp,
]);
const benchmarkCount = computed(
  () => allRows.value.filter((row) => !row.name.startsWith("GEOMEAN")).length,
);
const maxAbsDiff = computed(() => maxAbsolute(allRows.value, "diff"));
const maxAbsDiffPct = computed(() => maxAbsolute(allRows.value, "diffPct"));
const warnings = computed(() => {
  const result: string[] = [];
  const a = sources.value[0];
  const b = sources.value[1];
  const aCoverage = sourceCoverage(a);
  const bCoverage = sourceCoverage(b);

  for (const source of [a, b]) {
    if (source?.clipboardError) result.push(source.clipboardError);
  }

  for (const source of [a, b]) {
    if (source?.runId === "custom" && !source.customCoverage) {
      result.push(
        props
          .t("comparisonCoverageMissing")
          .replace("{0}", source.id.toUpperCase()),
      );
    }
  }
  if (aCoverage && bCoverage && !sameCoverage(aCoverage, bCoverage)) {
    result.push(
      props
        .t("comparisonCoverageMismatch")
        .replace("{0}", aCoverage)
        .replace("{1}", bCoverage),
    );
  }

  const aVersion = sourceSpecVersion(a);
  const bVersion = sourceSpecVersion(b);
  if (aVersion && bVersion && aVersion !== bVersion) {
    result.push(
      props
        .t("comparisonSpecMismatch")
        .replace("{0}", aVersion)
        .replace("{1}", bVersion),
    );
  }

  const aRun = a.runs.find((item) => item.runId === a.runId);
  const bRun = b.runs.find((item) => item.runId === b.runId);
  for (const [run, source] of [
    [aRun, a],
    [bRun, b],
  ] as const) {
    if (run?.note) {
      result.push(
        props
          .t("comparisonNote")
          .replace("{0}", source.id.toUpperCase())
          .replace("{1}", run.note),
      );
    }
  }

  return result;
});

function buildRows(category: SpecCategory): ComparisonRow[] {
  const a = sources.value[0]?.payload || {};
  const b = sources.value[1]?.payload || {};
  const names = SPEC_BENCHMARK_GROUPS[props.specVersion][category].benchmarks;

  const rows = names.map((name) =>
    makeRow(name, metric(a[name]), metric(b[name])),
  );
  const aGeo = geometricMean(rows.map((row) => row.a));
  const bGeo = geometricMean(rows.map((row) => row.b));
  return [
    makeRow(getSpecGeomeanName(props.specVersion, category), aGeo, bGeo),
    ...rows,
  ];
}

function makeRow(
  name: string,
  av: number | null,
  bv: number | null,
): ComparisonRow {
  const diff = av !== null && bv !== null ? bv - av : null;
  return {
    name,
    a: av,
    b: bv,
    diff,
    diffPct: diff !== null && av ? (diff / av) * 100 : null,
  };
}

function geometricMean(values: Array<number | null>): number | null {
  const valid = values.filter(
    (value): value is number => value !== null && value > 0,
  );
  return valid.length
    ? Math.exp(
        valid.reduce((sum, value) => sum + Math.log(value), 0) / valid.length,
      )
    : null;
}

function metric(entry: ReportPayload[string] | undefined): number | null {
  if (!entry) return null;
  const value = entry.score ?? entry.ipc;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sourceName(source?: ComparisonSource) {
  if (!source) return "";
  if (source.runId === "custom") {
    return source.customCommit && source.customDate
      ? `${props.t("comparisonClipboard")} · ${source.customCommit} · ${source.customDate}`
      : props.t("comparisonClipboard");
  }
  const run = source.runs.find((item) => item.runId === source.runId);
  return run
    ? `${sourceDatasetName(source)} · ${run.runId} · ${run.hash.slice(0, 8)} · ${formatDisplayDate(run.dateMs)}`
    : source.label;
}

function sourceDatasetName(source: ComparisonSource): string {
  if (!source.dataset) return "";
  return [source.dataset.branch, source.dataset.subset]
    .filter(Boolean)
    .join(" · ");
}

function sourceCoverage(source?: ComparisonSource): string | undefined {
  if (!source) return undefined;
  if (source.runId === "custom") return source.customCoverage;
  return (
    source.runs.find((item) => item.runId === source.runId)?.coverage ||
    undefined
  );
}

function sourceSpecVersion(source?: ComparisonSource): SpecVersion | undefined {
  if (!source) return undefined;
  if (source.runId === "custom") return source.customSpecVersion;
  return source.runs.find((item) => item.runId === source.runId)?.specVersion;
}

function sameCoverage(a: string, b: string): boolean {
  const aValue = Number.parseFloat(a);
  const bValue = Number.parseFloat(b);
  return Number.isFinite(aValue) && Number.isFinite(bValue)
    ? aValue === bValue
    : a === b;
}

function maxAbsolute(rows: ComparisonRow[], key: "diff" | "diffPct"): number {
  return rows.reduce((max, row) => Math.max(max, Math.abs(row[key] ?? 0)), 0);
}

function conditionalStyle(value: number | null, max: number) {
  if (value === null || value === 0 || max === 0) return undefined;
  const ratio = Math.min(Math.abs(value) / max, 1);
  const target = value < 0 ? MAX_NEGATIVE_COLOR : MAX_POSITIVE_COLOR;
  const color = target.map((channel) =>
    Math.round(255 + (channel - 255) * ratio),
  );
  return { backgroundColor: `rgb(${color.join(", ")})` };
}

const MAX_POSITIVE_COLOR = [99, 190, 123] as const;
const MAX_NEGATIVE_COLOR = [248, 105, 107] as const;

function format(value: number | null, isGeomean = false) {
  return value === null ? "—" : value.toFixed(isGeomean ? 6 : 3);
}

function formatPct(value: number | null) {
  return value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(3)}%`;
}

async function exportPng(): Promise<string> {
  const source = exportRoot.value;
  if (!source) throw new Error("comparison panel is not mounted");

  const clone = source.cloneNode(true) as HTMLElement;
  const width = EXPORT_LAYOUT_WIDTH;
  const renderHost = document.createElement("div");
  renderHost.style.position = "fixed";
  renderHost.style.left = "-100000px";
  renderHost.style.top = "0";
  renderHost.style.width = `${width}px`;
  renderHost.style.height = "auto";
  renderHost.style.overflow = "visible";

  clone.style.position = "static";
  clone.style.width = `${width}px`;
  clone.style.height = "auto";
  clone.style.minHeight = "0";
  clone.style.backgroundColor = "#ffffff";
  clone.style.borderRadius = "var(--radius-card)";
  clone.style.overflow = "hidden";

  clone.querySelectorAll<HTMLElement>(".comparison-layout").forEach((node) => {
    node.style.height = "auto";
    node.style.minHeight = "0";
    node.style.overflow = "visible";
  });
  clone.querySelectorAll<HTMLElement>(".panel-shell").forEach((node) => {
    node.style.height = "auto";
    node.style.minHeight = "0";
    node.style.flex = "none";
    node.style.overflow = "visible";
  });
  clone.querySelectorAll<HTMLElement>(".comparison-tables").forEach((node) => {
    node.style.height = "auto";
    node.style.flex = "none";
  });
  clone
    .querySelectorAll<HTMLElement>(".table-group, .table-wrap")
    .forEach((node) => {
      node.style.height = "auto";
      node.style.minHeight = "0";
      node.style.flex = "none";
      node.style.overflow = "visible";
    });

  renderHost.appendChild(clone);
  document.body.appendChild(renderHost);
  try {
    const height = Math.ceil(clone.getBoundingClientRect().height);
    if (!height) throw new Error("comparison panel has no visible content");
    return await toPng(clone, {
      width,
      height,
      pixelRatio: EXPORT_PIXEL_RATIO,
      cacheBust: true,
    });
  } finally {
    renderHost.remove();
  }
}

defineExpose({ exportPng });
</script>

<style scoped>
.comparison-layout {
  display: flex;
  min-height: 0;
  flex: 1;
  height: 100%;
}
.comparison-tables {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  min-height: 0;
  flex: 1;
  height: 0;
  margin: 0 18px 18px;
}
.table-group {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.table-group h3 {
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--muted);
}
.comparison-warnings {
  margin: -4px 0 12px;
  padding: 9px 12px;
  border: 1px solid #f3cf77;
  border-radius: 8px;
  background: #fff8e5;
  color: #7a5510;
  font-size: 13px;
  font-weight: 700;
}
.comparison-warnings > div + div {
  margin-top: 4px;
}
.table-wrap {
  overflow: auto;
  flex: 1;
  min-height: 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  font-weight: 600;
}
th,
td {
  padding: 6px 10px;
  border-bottom: 1px solid #edf0f5;
  text-align: right;
  white-space: nowrap;
}
th:first-child,
td:first-child {
  text-align: left;
}
th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 1;
}
td:first-child {
  font-weight: 700;
}
.geomean-row td {
  font-weight: 800;
}
.empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-weight: 700;
  min-height: 280px;
}
@media (max-width: 960px) {
  .comparison-tables {
    grid-template-columns: 1fr;
  }
}
</style>
