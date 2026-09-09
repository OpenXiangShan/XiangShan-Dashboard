<template>
  <div id="app-root">
    <header class="topbar">
      <div class="brand">
        <img class="brand-mark" src="./assets/logo.svg" alt="" />
        <div>
          <h1>XiangShan Dashboard</h1>
          <div class="muted link-row">
            <a
              class="gh-link"
              href="https://github.com/OpenXiangShan/XiangShan-Dashboard"
              target="_blank"
              rel="noreferrer"
            >
              <span class="gh-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" role="img" focusable="false">
                  <path
                    d="M8 0a8 8 0 0 0-2.53 15.6c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2 .37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.19 0 .21.15.46.55.38A8 8 0 0 0 8 0Z"
                  />
                </svg>
              </span>
              <span>Dashboard</span>
            </a>
            <span>·</span>
            <a
              class="gh-link"
              href="https://github.com/OpenXiangShan/XiangShan"
              target="_blank"
              rel="noreferrer"
            >
              <span class="gh-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" role="img" focusable="false">
                  <path
                    d="M8 0a8 8 0 0 0-2.53 15.6c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2 .37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.19 0 .21.15.46.55.38A8 8 0 0 0 8 0Z"
                  />
                </svg>
              </span>
              <span>XiangShan</span>
            </a>
          </div>
        </div>
      </div>
      <DashboardHero
        :tabs="tabs"
        :selected-tab-id="selectedTabId"
        :tab-title="displayTabTitle"
        :runs-label="t('runsLabel')"
        :benchmarks-label="t('testcasesLabel')"
        :run-count="filteredRuns.length"
        :benchmark-count="
          activeTab.kind === 'comparison'
            ? comparisonBenchmarkCount
            : selectedBenchmarks.length
        "
        :show-badges="activeTab.kind === 'chart'"
        :show-benchmark-badge="activeTab.kind === 'comparison'"
        @tab-change="onTabChange"
      />
    </header>

    <main class="content">
      <div v-if="activeTab.kind === 'chart'" class="tab-workspace">
        <aside class="panel-sidebar">
          <RangeSelector
            :t="t"
            :tab="activeChartTab"
            :subsets="chartSubsets"
            :branches="branches"
            :selected-branch="selectedBranch"
            :selected-subset="selectedSubset"
            :start-date-str="startDateStr"
            :end-date-str="endDateStr"
            :active-quick-preset="quickRangePreset"
            @branch-change="onBranchChange"
            @subset-change="onSubsetChange"
            @start-date-change="onStartDateChange"
            @end-date-change="onEndDateChange"
            @set-quick-preset="setQuickPreset"
          />
          <BenchmarkSelector
            :t="t"
            :benchmarks="availableBenchmarks"
            :selected="selectedBenchmarks"
            :show-spec-buttons="activeChartTab.supportsSpecButtons"
            @select-default="onSelectDefault"
            @select-all="onSelectAll"
            @clear-selection="onClearSelection"
            @select-spec="onSelectSpec"
            @select-geomean="onSelectGeomean"
            @toggle-benchmark="onToggleBenchmark"
          />
          <Exporter
            :t="t"
            :disabled="
              selectedBenchmarks.length === 0 || filteredRuns.length === 0
            "
            :on-export="exportChartPng"
          />
        </aside>
        <MetricChartPanel
          ref="metricChartPanel"
          :tab="activeChartTab"
          :title="t(activeChartTab.titleKey)"
          :summary="chartSummary"
          :runs="filteredRuns"
          :selected-benchmarks="selectedBenchmarks"
          :run-data-by-hash="runDataByHash"
          :no-data-text="chartEmptyText"
          :geomean-missing="geomeanMissing"
          :spec-version="activeSpecVersion"
          :t="t"
        />
      </div>
      <div v-else class="comparison-workspace">
        <aside class="panel-sidebar">
          <CoverageSelector
            :t="t"
            :coverages="comparisonCoverages"
            :selected-coverage-id="selectedComparisonCoverageId"
            @coverage-change="onComparisonCoverageChange"
          />
          <ComparisonSelector
            v-for="source in comparisonSources"
            :key="source.id"
            :t="t"
            :source="source"
            :datasets="activeComparisonDatasets"
            :on-paste="pasteComparisonSource"
            :show-swap="source.id === 'b'"
            :on-swap="swapComparisonSources"
            @dataset-change="onComparisonDatasetChange(source.id, $event)"
            @run-change="onComparisonRunChange(source.id, $event)"
            @paste-error="onComparisonPasteError(source.id, $event)"
          />
          <Exporter
            :t="t"
            :disabled="comparisonBenchmarkCount === 0"
            :on-export="exportComparisonPng"
          />
        </aside>
        <ComparisonPanel
          ref="comparisonPanel"
          :t="t"
          :sources="comparisonSources"
          :coverage="selectedComparisonCoverage"
          :spec-version="comparisonSpecVersion"
        />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import DashboardHero from "./components/DashboardHero.vue";
import RangeSelector from "./components/sidebars/RangeSelector.vue";
import BenchmarkSelector from "./components/sidebars/BenchmarkSelector.vue";
import ComparisonSelector from "./components/sidebars/ComparisonSelector.vue";
import CoverageSelector from "./components/sidebars/CoverageSelector.vue";
import Exporter from "./components/sidebars/Exporter.vue";
import MetricChartPanel from "./components/panels/MetricChartPanel.vue";
import ComparisonPanel from "./components/panels/ComparisonPanel.vue";
import { DASHBOARD_TABS, type ChartConfig } from "./config/tabs";
import { useLocale } from "./composables/useLocale";
import { useDashboardSettings } from "./composables/useDashboardSettings";
import type { QuickRangePreset } from "./composables/useDashboardSettings";
import {
  isSpecBenchmark,
  selectDefault,
  selectSpecCategory,
  toggleSelection,
} from "./composables/useBenchmarkSelection";
import {
  getSpecGeomeanName,
  SPEC_VERSIONS,
  specVersionFromText,
  specVersionFromSubset,
  type SpecCategory,
  type SpecVersion,
} from "./config/spec";
import {
  detectSpecVersion,
  normalizeReportPayload,
} from "./services/benchmarkService";
import {
  formatDisplayDate,
  formatInputDate,
  getDateRange,
  loadBranchList,
  loadReport,
  loadRunIndex,
  loadSubsetList,
} from "./services/dataService";
import type { NormalizedRun, ReportPayload } from "./types/data";
import type {
  ComparisonCoverage,
  ComparisonDataset,
  ComparisonSource,
} from "./types/comparison";
const dayMs = 24 * 60 * 60 * 1000;
const defaultQuickRangePreset: QuickRangePreset = "lastWeek";
const tabs = DASHBOARD_TABS;
const regressionTabs = tabs.filter(
  (tab): tab is ChartConfig =>
    tab.kind === "chart" && tab.metricKey === "score",
);
const { t } = useLocale();
const {
  state: settings,
  load: loadSettings,
  save: saveSettings,
} = useDashboardSettings();

const selectedTabId = ref(settings.selectedTabId);
const activeTab = computed(
  () => tabs.find((tab) => tab.id === selectedTabId.value) || tabs[0],
);
const defaultChartTab = DASHBOARD_TABS.find(
  (tab): tab is ChartConfig => tab.kind === "chart",
)!;
const activeChartTab = computed(() =>
  activeTab.value.kind === "chart" ? activeTab.value : defaultChartTab,
);

function chartTabHasSubsets(tab: ChartConfig): boolean {
  return tab.metricKey === "score";
}

function displayTabTitle(tab: (typeof tabs)[number]) {
  return t(tab.titleKey);
}

const branches = ref<string[]>([]);
const chartSubsets = ref<string[]>([]);
const selectedBranch = ref(settings.selectedBranch);
const selectedSubset = ref(settings.selectedSubset);
const activeChartSubset = computed(() => {
  const subsets = chartSubsets.value;
  if (!subsets.length) return undefined;
  return subsets.includes(selectedSubset.value)
    ? selectedSubset.value
    : subsets[0];
});
const startDateStr = ref(settings.startDateStr);
const endDateStr = ref(settings.endDateStr);
const quickRangePreset = ref<QuickRangePreset | null>(
  settings.quickRangePreset,
);

const chartSummary = computed(() => {
  const parts = [selectedBranch.value || t("branch")];
  if (chartSubsets.value.length && activeChartSubset.value) {
    parts.push(activeChartSubset.value);
  }

  const first = filteredRuns.value[0];
  const last = filteredRuns.value[filteredRuns.value.length - 1];
  if (first && last) {
    if (quickRangePreset.value === "lastTenRuns") {
      parts.push(
        `${t("lastTenRuns")} · ${first.hash.slice(0, 8)} ~ ${last.hash.slice(0, 8)}`,
      );
    } else {
      parts.push(
        `${formatDisplayDate(first.dateMs)} · ${first.hash.slice(0, 8)} ~ ${formatDisplayDate(last.dateMs)} · ${last.hash.slice(0, 8)}`,
      );
    }
  }
  return parts.join(" · ");
});

const allRuns = ref<NormalizedRun[]>([]);
const filteredRuns = ref<NormalizedRun[]>([]);
const runDataByHash = ref<Record<string, ReportPayload>>({});

const availableBenchmarks = ref<string[]>([]);
const selectedBenchmarks = ref<string[]>([]);
const errorText = ref("");
const isHydrating = ref(true);
const isLoading = ref(false);
const loadingPath = ref("");

const geomeanMissing = ref<Record<number, Record<string, string[]>>>({});

const comparisonSources = ref<ComparisonSource[]>([
  {
    id: "a",
    label: t("comparisonSourceA"),
    runs: [],
    runId: "",
  },
  {
    id: "b",
    label: t("comparisonSourceB"),
    runs: [],
    runId: "",
  },
]);
const comparisonDatasets = ref<ComparisonDataset[]>([]);
const comparisonCoverages = computed<ComparisonCoverage[]>(() =>
  regressionTabs.map((tab) => ({
    id: tab.id,
    label: t(
      tab.id === "score-nightly"
        ? "comparisonCoverageNightly"
        : "comparisonCoverageWeekly",
    ).replace("{0}", tab.coverage || ""),
  })),
);
const selectedComparisonCoverageId = ref(
  comparisonCoverages.value[0]?.id || "",
);
const selectedComparisonCoverage = computed(
  () =>
    regressionTabs.find((tab) => tab.id === selectedComparisonCoverageId.value)
      ?.coverage || "",
);
const activeComparisonDatasets = computed(() =>
  comparisonDatasets.value.filter(
    (dataset) => dataset.tab.id === selectedComparisonCoverageId.value,
  ),
);
type ExportablePanel = {
  exportPng: () => Promise<string>;
};
const comparisonPanel = ref<ExportablePanel | null>(null);
const metricChartPanel = ref<ExportablePanel | null>(null);
const activeSpecVersion = computed(() =>
  chartSubsets.value.length
    ? specVersionFromSubset(activeChartSubset.value)
    : activeChartTab.value.defaultSpecVersion,
);
const comparisonSpecVersion = computed(() => {
  const sourceVersion = comparisonSources.value
    .map((source) =>
      source.runId === "custom"
        ? source.customSpecVersion ||
          detectSpecVersion(Object.keys(source.payload || {}))
        : source.runs.find((run) => run.runId === source.runId)?.specVersion,
    )
    .find((version) => version !== undefined);
  if (sourceVersion) return sourceVersion;
  const dataset = comparisonSources.value
    .map((source) => source.dataset)
    .find((value) => value !== undefined);
  return dataset ? specVersionFromSubset(dataset.subset) : SPEC_VERSIONS[0];
});

const comparisonBenchmarkCount = computed(() => {
  const payloads = comparisonSources.value
    .map((source) => source.payload || {})
    .filter((payload) => Object.keys(payload).length > 0);
  if (!payloads.length) return 0;
  const names = new Set(payloads.flatMap((payload) => Object.keys(payload)));
  const countGroup = (category: SpecCategory) =>
    Array.from(names).filter(
      (name) =>
        !name.startsWith("GEOMEAN") &&
        isSpecBenchmark(name, comparisonSpecVersion.value, category),
    ).length;
  const intCount = countGroup("int");
  const fpCount = countGroup("fp");
  return intCount + fpCount + Number(intCount > 0) + Number(fpCount > 0);
});

function comparisonDatasetId(
  tab: ChartConfig,
  branch: string,
  subset: string,
): string {
  return `${tab.id}:${branch}:${subset}`;
}

function withDefaultFirst(values: string[], defaultValue: string): string[] {
  return defaultValue && values.includes(defaultValue)
    ? [defaultValue, ...values.filter((value) => value !== defaultValue)]
    : values;
}

async function loadComparisonDatasets() {
  const datasetsByTab = await Promise.all(
    regressionTabs.map(async (tab) => {
      const branchConfig = await loadBranchList(tab);
      const branches = withDefaultFirst(
        branchConfig.branches,
        branchConfig.default,
      );
      return Promise.all(
        branches.map(async (branch) => ({
          tab,
          branch,
          subsetConfig: await loadSubsetList(tab, branch),
        })),
      );
    }),
  );
  comparisonDatasets.value = datasetsByTab.flatMap((branchDatasets) =>
    branchDatasets.flatMap(({ tab, branch, subsetConfig }) =>
      withDefaultFirst(subsetConfig.subsets, subsetConfig.default).map(
        (subset) => ({
          id: comparisonDatasetId(tab, branch, subset),
          label: `${branch} · ${subset}`,
          tab,
          branch,
          subset,
        }),
      ),
    ),
  );
}

async function loadComparisonSource(source: ComparisonSource) {
  source.dataset =
    activeComparisonDatasets.value.find(
      (dataset) => dataset.id === source.dataset?.id,
    ) || activeComparisonDatasets.value[0];
  if (!source.dataset) return;
  const { tab, branch, subset } = source.dataset;
  source.runs = await loadRunIndex(tab, branch, subset);
  if (!source.runs.some((run) => run.runId === source.runId))
    source.runId = source.runs[source.runs.length - 1]?.runId || "";
  const run = source.runs.find((item) => item.runId === source.runId);
  source.payload = run
    ? await loadReport(tab, branch, run.hash, subset)
    : undefined;
}

async function loadComparisonSources() {
  setLoading();
  try {
    await loadComparisonDatasets();
    await Promise.all(
      comparisonSources.value
        .filter((source) => source.runId !== "custom")
        .map(loadComparisonSource),
    );
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err);
  } finally {
    finishLoading();
  }
}

function resetComparisonSource(source: ComparisonSource) {
  source.label = t(
    source.id === "a" ? "comparisonSourceA" : "comparisonSourceB",
  );
  source.dataset = undefined;
  source.runs = [];
  source.runId = "";
  source.payload = undefined;
  source.customCommit = undefined;
  source.customDate = undefined;
  source.customCoverage = undefined;
  source.customSpecVersion = undefined;
  source.clipboardError = undefined;
}

async function onComparisonCoverageChange(coverageId: string) {
  if (coverageId === selectedComparisonCoverageId.value) return;
  selectedComparisonCoverageId.value = coverageId;
  comparisonSources.value.forEach(resetComparisonSource);
  setLoading();
  try {
    await Promise.all(comparisonSources.value.map(loadComparisonSource));
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : String(err);
  } finally {
    finishLoading();
  }
}

async function onComparisonDatasetChange(id: "a" | "b", datasetId: string) {
  const source = comparisonSources.value.find((item) => item.id === id);
  if (!source) return;
  const dataset = comparisonDatasets.value.find(
    (item) => item.id === datasetId,
  );
  if (!dataset) return;
  source.label = t(
    source.id === "a" ? "comparisonSourceA" : "comparisonSourceB",
  );
  source.customCommit = undefined;
  source.customDate = undefined;
  source.customCoverage = undefined;
  source.customSpecVersion = undefined;
  source.clipboardError = undefined;
  source.dataset = dataset;
  source.runs = [];
  source.runId = "";
  source.payload = undefined;
  await loadComparisonSource(source);
}

async function onComparisonRunChange(id: "a" | "b", runId: string) {
  const source = comparisonSources.value.find((item) => item.id === id);
  if (!source) return;
  source.label = t(
    source.id === "a" ? "comparisonSourceA" : "comparisonSourceB",
  );
  source.customCommit = undefined;
  source.customDate = undefined;
  source.customCoverage = undefined;
  source.customSpecVersion = undefined;
  source.clipboardError = undefined;
  source.runId = runId;
  if (!source.dataset) return;
  const run = source.runs.find((item) => item.runId === runId);
  source.payload = run
    ? await loadReport(
        source.dataset.tab,
        source.dataset.branch,
        run.hash,
        source.dataset.subset,
      )
    : undefined;
}

function parseClipboardReport(text: string): ReportPayload {
  const parsed: ReportPayload = {};
  try {
    const json = JSON.parse(text) as unknown;
    if (json && typeof json === "object" && !Array.isArray(json)) {
      for (const [name, raw] of Object.entries(
        json as Record<string, unknown>,
      )) {
        if (typeof raw === "number") parsed[name] = { score: raw };
        else if (raw && typeof raw === "object") {
          const entry = raw as Record<string, unknown>;
          const value =
            typeof entry.score === "number"
              ? entry.score
              : typeof entry.ipc === "number"
                ? entry.ipc
                : null;
          if (value !== null)
            parsed[name] =
              typeof entry.score === "number"
                ? { score: value }
                : { ipc: value };
        }
      }
    }
  } catch {
    /* fall through to score.txt parser */
  }
  if (Object.keys(parsed).length) return parsed;
  for (const line of text.split(/\r?\n/)) {
    const match =
      /^\s*((?:\d+\.)?\w+)\s+[\d.NaN]+\s+[\d.NaN]+\s+([\d.NaN]+)/.exec(line);
    if (match && match[2] !== "NaN")
      parsed[match[1]] = { score: Number(match[2]) };
  }
  if (!Object.keys(parsed).length)
    throw new Error(t("comparisonClipboardError"));
  return parsed;
}

function extractClipboardMetadata(text: string): {
  commit?: string;
  date?: string;
  coverage?: string;
  specVersion?: SpecVersion;
} {
  const match = /(?:^|[\\/\s])cr(\d{6})-([0-9a-f]{7,40})-/i.exec(text);
  const metadata: {
    commit?: string;
    date?: string;
    coverage?: string;
    specVersion?: SpecVersion;
  } = {
    coverage: /(?:^|[-_/])(\d+(?:\.\d+)?c)(?=\.txt|[-_/\s]|$)/i.exec(text)?.[1],
    specVersion: specVersionFromText(text),
  };
  if (!match) return metadata;

  const [, compactDate, commit] = match;
  const year = 2000 + Number(compactDate.slice(0, 2));
  const month = Number(compactDate.slice(2, 4));
  const day = Number(compactDate.slice(4, 6));
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  )
    return metadata;
  metadata.commit = commit;
  metadata.date = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  return metadata;
}

async function pasteComparisonSource(id: "a" | "b") {
  if (!navigator.clipboard?.readText)
    throw new Error(t("comparisonClipboardDenied"));
  const text = await navigator.clipboard.readText();
  const parsed = parseClipboardReport(text);
  const metadata = extractClipboardMetadata(text);
  const specVersion =
    metadata.specVersion || detectSpecVersion(Object.keys(parsed));
  const payload = normalizeReportPayload(parsed, specVersion);
  const source = comparisonSources.value.find((item) => item.id === id);
  if (source) {
    source.payload = payload;
    source.runId = "custom";
    source.customCommit = metadata.commit;
    source.customDate = metadata.date;
    source.customCoverage = metadata.coverage;
    source.customSpecVersion =
      specVersion || detectSpecVersion(Object.keys(payload));
    source.clipboardError = undefined;
  }
}

function onComparisonPasteError(id: "a" | "b", message: string) {
  const source = comparisonSources.value.find((item) => item.id === id);
  if (source) source.clipboardError = message;
}

function comparisonFilePart(value: string): string {
  return (
    value
      .replace(/[^a-z0-9._-]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "source"
  );
}

function comparisonSourceFileName(source?: ComparisonSource): string {
  if (!source) return "source";
  if (source.runId === "custom") {
    return source.customCommit
      ? `clipboard-${source.customCommit.slice(0, 12)}`
      : "clipboard";
  }
  const run = source.runs.find((item) => item.runId === source.runId);
  return run
    ? `${source.dataset?.id || "dataset"}-${run.runId}-${run.hash.slice(0, 8)}`
    : source.label;
}

async function exportComparisonPng() {
  const dataUrl = await comparisonPanel.value?.exportPng();
  if (!dataUrl) throw new Error(t("exportError"));

  const sourceA = comparisonFilePart(
    comparisonSourceFileName(comparisonSources.value[0]),
  );
  const sourceB = comparisonFilePart(
    comparisonSourceFileName(comparisonSources.value[1]),
  );
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = `performance-comparison-${sourceA}-vs-${sourceB}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function chartFilePart(value: string): string {
  return (
    value
      .replace(/[^a-z0-9._-]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "chart"
  );
}

async function exportChartPng() {
  const dataUrl = await metricChartPanel.value?.exportPng();
  if (!dataUrl) throw new Error(t("exportError"));

  const tabName = chartFilePart(t(activeChartTab.value.titleKey));
  const branchName = chartFilePart(selectedBranch.value || "branch");
  const subsetName = activeChartSubset.value
    ? `-${chartFilePart(activeChartSubset.value)}`
    : "";
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = `metric-chart-${tabName}-${branchName}${subsetName}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function swapComparisonSources() {
  const sourceA = comparisonSources.value.find((source) => source.id === "a");
  const sourceB = comparisonSources.value.find((source) => source.id === "b");
  if (!sourceA || !sourceB) return;

  const stateA = {
    dataset: sourceA.dataset,
    runs: sourceA.runs,
    runId: sourceA.runId,
    payload: sourceA.payload,
    customCommit: sourceA.customCommit,
    customDate: sourceA.customDate,
    customCoverage: sourceA.customCoverage,
    customSpecVersion: sourceA.customSpecVersion,
    clipboardError: sourceA.clipboardError,
  };
  const stateB = {
    dataset: sourceB.dataset,
    runs: sourceB.runs,
    runId: sourceB.runId,
    payload: sourceB.payload,
    customCommit: sourceB.customCommit,
    customDate: sourceB.customDate,
    customCoverage: sourceB.customCoverage,
    customSpecVersion: sourceB.customSpecVersion,
    clipboardError: sourceB.clipboardError,
  };

  Object.assign(sourceA, stateB, {
    label: t("comparisonSourceA"),
  });
  Object.assign(sourceB, stateA, {
    label: t("comparisonSourceB"),
  });
}

const chartEmptyText = computed(() => {
  if (isLoading.value) {
    return loadingPath.value
      ? `${t("loading")}: ${loadingPath.value}`
      : t("loading");
  }
  return errorText.value || t("noData");
});

function setLoading(path = "") {
  isLoading.value = true;
  loadingPath.value = path;
}

function finishLoading() {
  isLoading.value = false;
  loadingPath.value = "";
}

interface ChartLoadRequest {
  generation: number;
  controller: AbortController;
  tab: ChartConfig;
}

interface ChartLoadContext extends ChartLoadRequest {
  branch: string;
  subset?: string;
  runs: NormalizedRun[];
}

let chartLoadGeneration = 0;
let chartLoadController: AbortController | undefined;

function beginChartLoad(): ChartLoadRequest {
  chartLoadController?.abort();
  chartLoadController = new AbortController();
  chartLoadGeneration += 1;
  return {
    generation: chartLoadGeneration,
    controller: chartLoadController,
    tab: activeChartTab.value,
  };
}

function invalidateChartLoad() {
  chartLoadController?.abort();
  chartLoadController = undefined;
  chartLoadGeneration += 1;
}

function isCurrentChartLoad(request: ChartLoadRequest): boolean {
  return request.generation === chartLoadGeneration;
}

function setChartLoading(request: ChartLoadRequest, path = "") {
  if (isCurrentChartLoad(request)) setLoading(path);
}

function finishChartLoad(request: ChartLoadRequest) {
  if (!isCurrentChartLoad(request)) return;
  chartLoadController = undefined;
  finishLoading();
}

function handleChartLoadError(request: ChartLoadRequest, err: unknown) {
  if (!isCurrentChartLoad(request)) return;
  if (err instanceof Error && err.name === "AbortError") return;
  console.error(err);
  errorText.value = err instanceof Error ? err.message : String(err);
  filteredRuns.value = [];
  availableBenchmarks.value = [];
  selectedBenchmarks.value = [];
}

function persist() {
  settings.selectedTabId = selectedTabId.value;
  settings.selectedSubset = selectedSubset.value;
  settings.selectedBranch = selectedBranch.value;
  settings.startDateStr = startDateStr.value;
  settings.endDateStr = endDateStr.value;
  settings.quickRangePreset = quickRangePreset.value;
  settings.selectedBenchmarks = selectedBenchmarks.value;
  saveSettings();
}

function syncSelection() {
  const selectedSet = new Set(selectedBenchmarks.value);
  const valid = availableBenchmarks.value.filter((tc) => selectedSet.has(tc));
  if (valid.length) {
    selectedBenchmarks.value = valid;
  } else {
    selectedBenchmarks.value = selectDefault(availableBenchmarks.value);
  }
}

async function refreshRuns(context: ChartLoadContext) {
  if (!isCurrentChartLoad(context)) return;
  if (
    quickRangePreset.value !== "lastTenRuns" &&
    (!startDateStr.value || !endDateStr.value)
  ) {
    return;
  }

  setChartLoading(context);
  const preset = quickRangePreset.value;
  const startDate = startDateStr.value;
  const endDate = endDateStr.value;
  let filtered: NormalizedRun[];
  if (preset === "lastTenRuns") {
    filtered = context.runs.slice(-10);
  } else {
    const { startMs, endMs } = getDateRange(startDate, endDate);
    filtered = context.runs.filter(
      (run) => run.dateMs >= startMs && run.dateMs <= endMs,
    );
  }
  filteredRuns.value = filtered;

  const dataByHash = { ...runDataByHash.value };
  const needed = filtered.filter((run) => !dataByHash[run.hash]);
  for (const run of needed) {
    setChartLoading(
      context,
      `${context.tab.datasetRoot}/${context.branch}/${context.subset ? `${context.subset}/` : ""}${run.hash}.json`,
    );
    const payload = await loadReport(
      context.tab,
      context.branch,
      run.hash,
      context.subset,
      context.controller.signal,
    );
    if (!isCurrentChartLoad(context)) return;
    dataByHash[run.hash] = payload;
    runDataByHash.value = { ...dataByHash };
  }

  const set = new Set<string>();
  for (const run of filtered) {
    const payload = dataByHash[run.hash];
    if (!payload) continue;
    Object.keys(payload).forEach((name) => set.add(name));
  }

  const specVersion = context.subset
    ? specVersionFromSubset(context.subset)
    : context.tab.defaultSpecVersion;
  set.add("GEOMEAN");
  const intGeomean = getSpecGeomeanName(specVersion, "int");
  const fpGeomean = getSpecGeomeanName(specVersion, "fp");
  const geomeanNames = ["GEOMEAN"];
  if (context.tab.supportsSpecButtons) {
    set.add(intGeomean);
    set.add(fpGeomean);
    geomeanNames.push(intGeomean, fpGeomean);
  }

  const available = Array.from(set).sort();
  const geomean: Record<number, Record<string, string[]>> = {};
  filtered.forEach((run, runIdx) => {
    const payload = dataByHash[run.hash];
    geomeanNames.forEach((name) => {
      let scopeTestcases = available.filter(
        (benchmark) =>
          !benchmark.startsWith("GEOMEAN") && !benchmark.startsWith("legacy"),
      );
      if (name === intGeomean) {
        scopeTestcases = scopeTestcases.filter((benchmark) =>
          isSpecBenchmark(benchmark, specVersion, "int"),
        );
      } else if (name === fpGeomean) {
        scopeTestcases = scopeTestcases.filter((benchmark) =>
          isSpecBenchmark(benchmark, specVersion, "fp"),
        );
      }
      const missing = scopeTestcases.filter((benchmark) => {
        if (!payload) return true;
        if (!Object.prototype.hasOwnProperty.call(payload, benchmark)) {
          return true;
        }
        const metricValue = payload[benchmark]?.[context.tab.metricKey];
        return typeof metricValue !== "number" || metricValue <= 0;
      });
      if (missing.length) {
        if (!geomean[runIdx]) geomean[runIdx] = {};
        geomean[runIdx][name] = missing;
      }
    });
  });
  if (!isCurrentChartLoad(context)) return;
  availableBenchmarks.value = available;
  geomeanMissing.value = geomean;
  syncSelection();
  persist();
}

async function refreshCurrentRuns() {
  if (activeTab.value.kind !== "chart") return;
  if (!selectedBranch.value) return;
  if (!allRuns.value.length || !allRuns.value.some((run) => run.hash)) return;
  if (
    quickRangePreset.value !== "lastTenRuns" &&
    (!startDateStr.value || !endDateStr.value)
  ) {
    return;
  }

  const request = beginChartLoad();
  errorText.value = "";
  try {
    await refreshRuns({
      ...request,
      branch: selectedBranch.value,
      subset: activeChartSubset.value,
      runs: [...allRuns.value],
    });
  } catch (err) {
    handleChartLoadError(request, err);
  } finally {
    finishChartLoad(request);
  }
}

async function loadCurrentTabData() {
  const request = beginChartLoad();
  errorText.value = "";
  allRuns.value = [];
  filteredRuns.value = [];
  availableBenchmarks.value = [];
  runDataByHash.value = {};
  geomeanMissing.value = {};

  try {
    setChartLoading(request, `${request.tab.datasetRoot}/branch.json`);
    const branchConfig = await loadBranchList(
      request.tab,
      request.controller.signal,
    );
    if (!isCurrentChartLoad(request)) return;
    branches.value = branchConfig.branches;
    const branch = branches.value.includes(selectedBranch.value)
      ? selectedBranch.value
      : branchConfig.default || branches.value[0] || "";
    selectedBranch.value = branch;

    if (chartTabHasSubsets(request.tab)) {
      setChartLoading(
        request,
        `${request.tab.datasetRoot}/${branch}/subset.json`,
      );
    }
    const subsetConfig = chartTabHasSubsets(request.tab)
      ? await loadSubsetList(request.tab, branch, request.controller.signal)
      : { default: "", subsets: [] };
    if (!isCurrentChartLoad(request)) return;
    chartSubsets.value = subsetConfig.subsets;
    const subset = chartSubsets.value.includes(selectedSubset.value)
      ? selectedSubset.value
      : subsetConfig.default || chartSubsets.value[0] || "";
    selectedSubset.value = subset;

    setChartLoading(
      request,
      `${request.tab.datasetRoot}/${branch}/${subset ? `${subset}/` : ""}data.json`,
    );
    const runs = await loadRunIndex(
      request.tab,
      branch,
      subset || undefined,
      request.controller.signal,
    );
    if (!isCurrentChartLoad(request)) return;
    allRuns.value = runs;
    if (
      quickRangePreset.value === "lastWeek" &&
      request.tab.id === "score-weekly"
    ) {
      quickRangePreset.value = "lastMonth";
    } else if (
      quickRangePreset.value === "last3Months" &&
      request.tab.id !== "score-weekly"
    ) {
      quickRangePreset.value = "lastMonth";
    }
    if (!quickRangePreset.value && (!startDateStr.value || !endDateStr.value)) {
      setQuickPreset(defaultQuickRangePreset, false);
    } else {
      setQuickPreset(quickRangePreset.value || defaultQuickRangePreset, false);
    }

    await refreshRuns({
      ...request,
      branch,
      subset: subset || undefined,
      runs,
    });
  } catch (err) {
    handleChartLoadError(request, err);
  } finally {
    finishChartLoad(request);
  }
}

function onSelectDefault() {
  selectedBenchmarks.value = selectDefault(availableBenchmarks.value);
  persist();
}

function onSelectAll() {
  selectedBenchmarks.value = [...availableBenchmarks.value];
  persist();
}

function onClearSelection() {
  selectedBenchmarks.value = [];
  persist();
}

function onSelectSpec(category: SpecCategory) {
  selectedBenchmarks.value = selectSpecCategory(
    availableBenchmarks.value,
    activeSpecVersion.value,
    category,
  );
  persist();
}

function onSelectGeomean() {
  selectedBenchmarks.value = availableBenchmarks.value.filter((name) =>
    name.startsWith("GEOMEAN"),
  );
  persist();
}

function onToggleBenchmark(name: string) {
  selectedBenchmarks.value = toggleSelection(
    availableBenchmarks.value,
    selectedBenchmarks.value,
    name,
  );
  persist();
}

function onTabChange(nextTabId: string) {
  if (nextTabId === selectedTabId.value) return;
  invalidateChartLoad();
  selectedTabId.value = nextTabId;
  persist();
}

async function onBranchChange(nextBranch: string) {
  if (nextBranch === selectedBranch.value) return;
  selectedBranch.value = nextBranch;
  await loadCurrentTabData();
}

async function onSubsetChange(nextSubset: string) {
  selectedSubset.value = nextSubset;
  persist();
  selectedBenchmarks.value = [];
  await loadCurrentTabData();
}

function onStartDateChange(value: string) {
  startDateStr.value = value;
  quickRangePreset.value = null;
  persist();
  void refreshCurrentRuns();
}

function onEndDateChange(value: string) {
  endDateStr.value = value;
  quickRangePreset.value = null;
  persist();
  void refreshCurrentRuns();
}

function setQuickPreset(preset: QuickRangePreset, shouldPersist = true) {
  quickRangePreset.value = preset;
  if (preset === "lastTenRuns") {
    if (shouldPersist) {
      persist();
      void refreshCurrentRuns();
    }
    return;
  }

  const days =
    preset === "lastWeek"
      ? 7
      : preset === "lastMonth"
        ? 31
        : preset === "last3Months"
          ? 90
          : 0;
  const end = new Date();
  end.setHours(23, 59, 59, 999);
  const start = new Date(end.getTime() - (days - 1) * dayMs);
  startDateStr.value = formatInputDate(start);
  endDateStr.value = formatInputDate(end);
  if (shouldPersist) {
    persist();
    void refreshCurrentRuns();
  }
}

watch(selectedTabId, async () => {
  if (isHydrating.value) return;
  if (activeTab.value.kind === "comparison") {
    invalidateChartLoad();
    await loadComparisonSources();
    return;
  }
  startDateStr.value = "";
  endDateStr.value = "";
  selectedBenchmarks.value = [];
  await loadCurrentTabData();
});

onMounted(async () => {
  isHydrating.value = true;
  try {
    loadSettings();
    selectedTabId.value = settings.selectedTabId;
    selectedBranch.value = settings.selectedBranch;
    selectedSubset.value = settings.selectedSubset;
    startDateStr.value = settings.startDateStr;
    endDateStr.value = settings.endDateStr;
    quickRangePreset.value = settings.quickRangePreset;
    selectedBenchmarks.value = settings.selectedBenchmarks;
    if (activeTab.value.kind === "comparison") {
      await loadComparisonSources();
    } else {
      await loadCurrentTabData();
    }
  } finally {
    isHydrating.value = false;
  }
});
</script>
