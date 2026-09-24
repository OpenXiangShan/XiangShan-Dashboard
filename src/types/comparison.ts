import type { NormalizedRun, ReportPayload } from "./data";
import type { SpecVersion } from "../config/spec";
import type { ChartConfig } from "../config/tabs";

export type ComparisonSourceId = "a" | "b";

export interface ComparisonCoverage {
  id: string;
  label: string;
}

export interface ComparisonDataset {
  id: string;
  label: string;
  tab: ChartConfig;
  branch: string;
  subset: string;
}

export interface ClipboardMetadata {
  commit?: string;
  date?: string;
  coverage?: string;
  specVersion?: SpecVersion;
  note?: string;
}

export interface ComparisonSource {
  id: ComparisonSourceId;
  label: string;
  dataset?: ComparisonDataset;
  runs: NormalizedRun[];
  runId: string;
  payload?: ReportPayload;
  clipboard?: ClipboardMetadata;
  clipboardError?: string;
}
