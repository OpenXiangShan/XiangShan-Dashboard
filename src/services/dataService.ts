import type { ChartConfig } from "../config/tabs";
import { specVersionFromSubset } from "../config/spec";
import { normalizeReportPayload } from "./benchmarkService";
import {
  assertBranchList,
  assertReportPayload,
  assertRunIndex,
  assertSubsetList,
  type NormalizedRun,
  type ReportPayload,
} from "../types/data";

async function fetchJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    throw new Error(`failed to fetch ${path}: ${response.status}`);
  }
  return response.json();
}

export async function loadBranchList(tab: ChartConfig, signal?: AbortSignal) {
  const payloadRaw = await fetchJson(`${tab.datasetRoot}/branch.json`, signal);
  return assertBranchList(payloadRaw);
}

export async function loadSubsetList(
  tab: ChartConfig,
  branch: string,
  signal?: AbortSignal,
) {
  const payloadRaw = await fetchJson(
    `${tab.datasetRoot}/${branch}/subset.json`,
    signal,
  );
  return assertSubsetList(payloadRaw);
}

function getDatasetPath(
  tab: ChartConfig,
  branch: string,
  subset?: string,
): string {
  if (subset) {
    return `${tab.datasetRoot}/${branch}/${subset}`;
  }
  return `${tab.datasetRoot}/${branch}`;
}

export async function loadRunIndex(
  tab: ChartConfig,
  branch: string,
  subset?: string,
  signal?: AbortSignal,
): Promise<NormalizedRun[]> {
  const indexRaw = await fetchJson(
    `${getDatasetPath(tab, branch, subset)}/data.json`,
    signal,
  );
  const index = assertRunIndex(indexRaw);
  return Object.entries(index.data)
    .map(([runId, entry]) => ({
      runId,
      hash: entry.hash,
      title: entry.title,
      dateMs: entry.date > 1e12 ? entry.date : entry.date * 1000,
      note: entry.note,
      coverage: tab.coverage,
      specVersion: subset
        ? specVersionFromSubset(subset)
        : tab.defaultSpecVersion,
    }))
    .sort((a, b) => Number(a.runId) - Number(b.runId));
}

export async function loadReport(
  tab: ChartConfig,
  branch: string,
  hash: string,
  subset?: string,
  signal?: AbortSignal,
): Promise<ReportPayload> {
  const payloadRaw = await fetchJson(
    `${getDatasetPath(tab, branch, subset)}/${hash}.json`,
    signal,
  );
  const payload = assertReportPayload(payloadRaw);
  return tab.metricKey === "score" ? normalizeReportPayload(payload) : payload;
}

export function formatDate(value: Date | number): string {
  const date = value instanceof Date ? value : new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function getDateRange(
  startDate: string,
  endDate: string,
): { startMs: number; endMs: number } {
  const start = new Date(`${startDate}T00:00:00`).getTime();
  const end = new Date(`${endDate}T23:59:59`).getTime();
  return { startMs: start, endMs: end };
}
