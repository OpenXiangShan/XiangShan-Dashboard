import type { MetricKey } from "../types/data";
import type { SpecVersion } from "./spec";

export type AxisMode = "run-id" | "date";

interface TabConfigBase {
  id: string;
  titleKey: string;
}

export interface ChartConfig extends TabConfigBase {
  kind: "chart";
  id: "ipc-commit" | "score-nightly" | "score-weekly";
  datasetRoot: string;
  metricKey: MetricKey;
  axisMode: AxisMode;
  supportsSpecButtons: boolean;
  defaultSpecVersion: SpecVersion;
  coverage: string | null;
}

export interface ComparisonConfig extends TabConfigBase {
  kind: "comparison";
  id: "performance-compare";
}

export type TabConfig = ChartConfig | ComparisonConfig;

export const DASHBOARD_TABS: TabConfig[] = [
  {
    kind: "chart",
    id: "ipc-commit",
    titleKey: "tabsTest",
    datasetRoot: "data/test",
    metricKey: "ipc",
    axisMode: "run-id",
    supportsSpecButtons: false,
    defaultSpecVersion: "06",
    coverage: null,
  },
  {
    kind: "chart",
    id: "score-nightly",
    titleKey: "tabsNightly",
    datasetRoot: "data/nightly",
    metricKey: "score",
    axisMode: "date",
    supportsSpecButtons: true,
    defaultSpecVersion: "06",
    coverage: "0.3c",
  },
  {
    kind: "chart",
    id: "score-weekly",
    titleKey: "tabsWeekly",
    datasetRoot: "data/weekly",
    metricKey: "score",
    axisMode: "date",
    supportsSpecButtons: true,
    defaultSpecVersion: "06",
    coverage: "1.0c",
  },
  {
    kind: "comparison",
    id: "performance-compare",
    titleKey: "tabsCompare",
  },
];
