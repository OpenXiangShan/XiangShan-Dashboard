export type MetricKey = "ipc" | "score" | "counter";
export type ReportPayload = Record<
  string,
  Partial<Record<MetricKey, number | null>>
>;
export interface NormalizedRun {
  runId: string;
  hash: string;
  title: string;
  dateMs: number;
  coverage: string | null;
}
