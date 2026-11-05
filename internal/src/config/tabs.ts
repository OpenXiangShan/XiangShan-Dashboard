import type { MetricKey } from "../types/data";

export interface ChartConfig {
  id: string;
  titleKey: string;
  metricKey: MetricKey;
  metricLabel: string;
  axisMode: "date" | "run-id";
}
