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
          <ComparisonSelector
            v-for="source in comparisonSources"
            :key="source.id"
            :t="t"
            :source="source"
            :datasets="comparisonDatasets"
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
import Exporter from "./components/sidebars/Exporter.vue";
import MetricChartPanel from "./components/panels/MetricChartPanel.vue";
import ComparisonPanel from "./components/panels/ComparisonPanel.vue";
import { DASHBOARD_TABS, type ChartConfig } from "./config/tabs";
import { useLocale } from "./composables/useLocale";
import { useDashboardSettings } from "./composables/useDashboardSettings";
import type { QuickRangePreset } from "./composables/useDashboardSettings";
import {
  detectSpecVersion,
  isSpecBenchmark,
  selectDefault,
  selectSpecCategory,
  toggleSelection,
} from "./composables/useBenchmarkSelection";
import {
  getSpecGeomeanName,
  specVersionFromText,
  specVersionFromSubset,
  type SpecCategory,
  type SpecVersion,
} from "./config/spec";
import {
  formatDisplayDate,
  formatInputDate,
  getDateRange,
  loadBranchList,
  loadReport,
  loadRunIndex,
} from "./services/dataService";
import type { NormalizedRun, ReportPayload } from "./types/data";
import type { ComparisonDataset, ComparisonSource } from "./types/comparison";
const dayMs = 24 * 60 * 60 * 1000;
const defaultQuickRangePreset: QuickRangePreset = "lastWeek";
const tabs = DASHBOARD_TABS;
const nightlyTab = tabs.find(
  (tab): tab is ChartConfig =>
    tab.kind === "chart" && tab.id === "score-nightly",
)!;
const weeklyTab = tabs.find(
  (tab): tab is ChartConfig =>
    tab.kind === "chart" && tab.id === "score-weekly",
)!;
const { t } = useLocale();
const {
  state: settings,
  load: loadSettings,
  save: saveSettings,
} = useDashboardSettings();

const selectedTabId = ref(settings.selectedTabId || tabs[0].id);
const activeTab = computed(
  () => tabs.find((tab) => tab.id === selectedTabId.value) || tabs[0],
);
const defaultChartTab = DASHBOARD_TABS.find(
  (tab): tab is ChartConfig => tab.kind === "chart",
)!;
const activeChartTab = computed(() =>
  activeTab.value.kind === "chart" ? activeTab.value : defaultChartTab,
);

function displayTabTitle(tab: (typeof tabs)[number]) {
  return t(tab.titleKey);
}

const branches = ref<string[]>([]);
const selectedBranch = ref(settings.selectedBranch || "");
const selectedSubset = ref(settings.selectedSubset || "");
const activeChartSubset = computed(() => {
  const subsets = activeChartTab.value.subsets || [];
  if (!subsets.length) return undefined;
  return subsets.includes(selectedSubset.value)
    ? selectedSubset.value
    : activeChartTab.value.defaultSubset || subsets[0];
});
const startDateStr = ref(settings.startDateStr || "");
const endDateStr = ref(settings.endDateStr || "");
const quickRangePreset = ref<QuickRangePreset | null>(
  settings.quickRangePreset ?? null,
);

const chartSummary = computed(() => {
  const parts = [selectedBranch.value || t("branch")];
  if (activeChartTab.value.subsets?.length && activeChartSubset.value) {
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
        `${formatDisplayDate(first.dateMs)}·${first.hash.slice(0, 8)} ~ ${formatDisplayDate(last.dateMs)}·${last.hash.slice(0, 8)}`,
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
type ExportablePanel = {
  exportPng: () => Promise<string>;
};
const comparisonPanel = ref<ExportablePanel | null>(null);
const metricChartPanel = ref<ExportablePanel | null>(null);
const activeSpecVersion = computed(() =>
  activeChartTab.value.subsets?.length
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
  return dataset?.subset
    ? specVersionFromSubset(dataset.subset)
    : dataset?.tab.defaultSpecVersion || nightlyTab.defaultSpecVersion;
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
  subset?: string,
): string {
  return [tab.id, branch, subset].filter(Boolean).join(":");
}

async function loadComparisonDatasets() {
  const [nightlyBranches, weeklyBranches] = await Promise.all([
    loadBranchList(nightlyTab),
    loadBranchList(weeklyTab),
  ]);
  comparisonDatasets.value = [
    ...nightlyBranches.map((branch) => ({
      id: comparisonDatasetId(nightlyTab, branch),
      label: `${branch} · ${t("comparisonNightly")}`,
      tab: nightlyTab,
      branch,
    })),
    ...weeklyBranches.flatMap((branch) =>
      (weeklyTab.subsets || []).map((subset) => ({
        id: comparisonDatasetId(weeklyTab, branch, subset),
        label: `${branch} · ${t("comparisonWeeklySubset").replace("{0}", subset)}`,
        tab: weeklyTab,
        branch,
        subset,
      })),
    ),
  ];
}

async function loadComparisonSource(source: ComparisonSource) {
  source.dataset =
    comparisonDatasets.value.find(
      (dataset) => dataset.id === source.dataset?.id,
    ) || comparisonDatasets.value[0];
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
    const match = /^\s*(\d+\.\w+)\s+[\d.NaN]+\s+[\d.NaN]+\s+([\d.NaN]+)/.exec(
      line,
    );
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
  const payload = parseClipboardReport(text);
  const metadata = extractClipboardMetadata(text);
  const source = comparisonSources.value.find((item) => item.id === id);
  if (source) {
    source.payload = payload;
    source.runId = "custom";
    source.customCommit = metadata.commit;
    source.customDate = metadata.date;
    source.customCoverage = metadata.coverage;
    source.customSpecVersion =
      metadata.specVersion || detectSpecVersion(Object.keys(payload));
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

async function refreshRuns() {
  if (
    quickRangePreset.value !== "lastTenRuns" &&
    (!startDateStr.value || !endDateStr.value)
  ) {
    return;
  }

  setLoading();
  try {
    if (quickRangePreset.value === "lastTenRuns") {
      filteredRuns.value = allRuns.value.slice(-10);
    } else {
      const { startMs, endMs } = getDateRange(
        startDateStr.value,
        endDateStr.value,
      );
      filteredRuns.value = allRuns.value.filter(
        (run) => run.dateMs >= startMs && run.dateMs <= endMs,
      );
    }

    const needed = filteredRuns.value.filter(
      (run) => !runDataByHash.value[run.hash],
    );
    for (const run of needed) {
      setLoading(
        `${activeChartTab.value.datasetRoot}/${selectedBranch.value}/${activeChartSubset.value ? `${activeChartSubset.value}/` : ""}${run.hash}.json`,
      );
      runDataByHash.value[run.hash] = await loadReport(
        activeChartTab.value,
        selectedBranch.value,
        run.hash,
        activeChartSubset.value,
      );
    }

    const set = new Set<string>();
    for (const run of filteredRuns.value) {
      const payload = runDataByHash.value[run.hash];
      if (!payload) continue;
      Object.keys(payload).forEach((name) => set.add(name));
    }

    set.add("GEOMEAN");
    const intGeomean = getSpecGeomeanName(activeSpecVersion.value, "int");
    const fpGeomean = getSpecGeomeanName(activeSpecVersion.value, "fp");
    const geomeanNames = ["GEOMEAN"];
    if (activeChartTab.value.supportsSpecButtons) {
      set.add(intGeomean);
      set.add(fpGeomean);
      geomeanNames.push(intGeomean, fpGeomean);
    }

    availableBenchmarks.value = Array.from(set).sort();

    // Calculate geomeanMissing after availableBenchmarks is set
    const geomean: Record<number, Record<string, string[]>> = {};
    filteredRuns.value.forEach((run, runIdx) => {
      const payload = runDataByHash.value[run.hash];
      geomeanNames.forEach((name) => {
        let scopeTestcases = availableBenchmarks.value.filter(
          (n) => !n.startsWith("GEOMEAN") && !n.startsWith("legacy"),
        );
        if (name === intGeomean) {
          scopeTestcases = scopeTestcases.filter((n) =>
            isSpecBenchmark(n, activeSpecVersion.value, "int"),
          );
        } else if (name === fpGeomean) {
          scopeTestcases = scopeTestcases.filter((n) =>
            isSpecBenchmark(n, activeSpecVersion.value, "fp"),
          );
        }
        const missing = scopeTestcases.filter((tc) => {
          if (!payload) return true;
          if (!Object.prototype.hasOwnProperty.call(payload, tc)) return true;
          const entry = payload?.[tc];
          const metricValue = entry?.[activeChartTab.value.metricKey];
          if (typeof metricValue !== "number" || metricValue <= 0) return true;
          return false;
        });
        if (missing.length) {
          if (!geomean[runIdx]) geomean[runIdx] = {};
          geomean[runIdx][name] = missing;
        }
      });
    });
    geomeanMissing.value = geomean;

    syncSelection();
    persist();
  } finally {
    finishLoading();
  }
}

async function loadCurrentTabData() {
  errorText.value = "";
  runDataByHash.value = {};

  try {
    if (
      activeChartTab.value.subsets?.length &&
      activeChartSubset.value !== selectedSubset.value
    ) {
      selectedSubset.value = activeChartSubset.value || "";
    }
    setLoading(`${activeChartTab.value.datasetRoot}/branch.json`);
    branches.value = await loadBranchList(activeChartTab.value);
    if (!branches.value.includes(selectedBranch.value)) {
      selectedBranch.value = branches.value[0] || "";
    }

    setLoading(
      `${activeChartTab.value.datasetRoot}/${selectedBranch.value}/${activeChartSubset.value ? `${activeChartSubset.value}/` : ""}data.json`,
    );
    allRuns.value = await loadRunIndex(
      activeChartTab.value,
      selectedBranch.value,
      activeChartSubset.value,
    );
    if (
      quickRangePreset.value === "lastWeek" &&
      activeChartTab.value.id === "score-weekly"
    ) {
      quickRangePreset.value = "lastMonth";
    } else if (
      quickRangePreset.value === "last3Months" &&
      activeChartTab.value.id !== "score-weekly"
    ) {
      quickRangePreset.value = "lastMonth";
    }
    if (!quickRangePreset.value && (!startDateStr.value || !endDateStr.value)) {
      setQuickPreset(defaultQuickRangePreset, false);
    } else {
      setQuickPreset(quickRangePreset.value || defaultQuickRangePreset, false);
    }

    await refreshRuns();
  } catch (err) {
    console.error(err);
    errorText.value = err instanceof Error ? err.message : String(err);
    filteredRuns.value = [];
    availableBenchmarks.value = [];
    selectedBenchmarks.value = [];
  } finally {
    finishLoading();
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
  selectedTabId.value = nextTabId;
  persist();
}

function onBranchChange(nextBranch: string) {
  selectedBranch.value = nextBranch;
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
}

function onEndDateChange(value: string) {
  endDateStr.value = value;
  quickRangePreset.value = null;
  persist();
}

function setQuickPreset(preset: QuickRangePreset, shouldPersist = true) {
  quickRangePreset.value = preset;
  if (preset === "lastTenRuns") {
    if (shouldPersist) {
      persist();
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
  }
}

watch(selectedTabId, async () => {
  if (isHydrating.value) return;
  if (activeTab.value.kind === "comparison") {
    await loadComparisonSources();
    return;
  }
  startDateStr.value = "";
  endDateStr.value = "";
  selectedBenchmarks.value = [];
  await loadCurrentTabData();
});

watch([selectedBranch, startDateStr, endDateStr], async () => {
  if (isHydrating.value) return;
  if (!selectedBranch.value) return;
  if (
    quickRangePreset.value !== "lastTenRuns" &&
    (!startDateStr.value || !endDateStr.value)
  ) {
    return;
  }

  if (!allRuns.value.length || !allRuns.value.some((run) => run.hash)) {
    return;
  }

  await refreshRuns();
});

watch(quickRangePreset, async (preset) => {
  if (isHydrating.value) return;
  if (!preset) return;
  if (!selectedBranch.value) return;
  if (!allRuns.value.length || !allRuns.value.some((run) => run.hash)) {
    return;
  }
  await refreshRuns();
});

watch(selectedBranch, async () => {
  if (isHydrating.value) return;
  if (!selectedBranch.value) return;
  allRuns.value = await loadRunIndex(
    activeChartTab.value,
    selectedBranch.value,
    activeChartSubset.value,
  );
  if (quickRangePreset.value === "lastWeek") {
    setQuickPreset("lastWeek", false);
  } else if (quickRangePreset.value === "lastMonth") {
    setQuickPreset("lastMonth", false);
  } else if (quickRangePreset.value === "last3Months") {
    setQuickPreset("last3Months", false);
  } else if (!quickRangePreset.value) {
    setQuickPreset(defaultQuickRangePreset, false);
  }
  runDataByHash.value = {};
  await refreshRuns();
});

onMounted(async () => {
  isHydrating.value = true;
  try {
    loadSettings();
    selectedTabId.value = settings.selectedTabId || tabs[0].id;
    if (selectedTabId.value.startsWith("score-weekly-")) {
      const legacySubset = selectedTabId.value.slice("score-weekly-".length);
      selectedTabId.value = "score-weekly";
      settings.selectedSubset = weeklyTab.subsets?.includes(legacySubset)
        ? legacySubset
        : weeklyTab.defaultSubset || weeklyTab.subsets?.[0] || "";
    } else if (
      settings.selectedSubset &&
      !weeklyTab.subsets?.includes(settings.selectedSubset)
    ) {
      settings.selectedSubset =
        weeklyTab.defaultSubset || weeklyTab.subsets?.[0] || "";
    }
    selectedBranch.value = settings.selectedBranch || "";
    selectedSubset.value = settings.selectedSubset || "";
    startDateStr.value = settings.startDateStr || "";
    endDateStr.value = settings.endDateStr || "";
    quickRangePreset.value = settings.quickRangePreset ?? null;
    selectedBenchmarks.value = settings.selectedBenchmarks || [];
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
