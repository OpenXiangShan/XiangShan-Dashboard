export interface Archive {
  id: string;
  run_id: string;
  backend?: "xs" | "gem5";
  repository?: string;
  actions_url?: string;
  label: string;
  config: string;
  benchmark_type: string;
  commit: string;
  path: string;
  available: boolean;
  metadata: Record<string, string>;
}
export interface RunSnapshot extends Archive {
  snapshot_id: string;
  profile_hash: string;
  processor_hash: string;
  completed_points: number;
  expected_points: number;
  scores: Array<{
    name: string;
    score: number | null;
    coverage: number | null;
  }>;
}
export interface Metric {
  id: string;
  name: string;
  kind: "yaml" | "raw";
  groups: string[];
}
export interface Row {
  name: string;
  benchmark: string;
  workload: string | null;
  point: number | null;
  metric: string;
  a: number | null;
  b: number | null;
  delta: number | null;
  delta_pct: number | null;
  status: string;
  coverage_a: number | null;
  coverage_b: number | null;
  weight_a: number | null;
  weight_b: number | null;
}
export interface Query {
  a: string;
  b: string;
  metrics: string[];
  groups: string[];
  level: "benchmark" | "simpoint";
  benchmarks: string[];
  simpoints: string[];
  sort: string;
  min_change: number;
}
export interface Result {
  runs: RunSnapshot[];
  warnings: string[];
  rows: Row[];
  row_count: number;
  benchmarks: string[];
  simpoints: string[];
  query: Query;
}
export interface Job {
  id: string;
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  stage: string;
  queue_position: number;
  elapsed_seconds?: number;
  query?: Query;
  error?: string;
  result?: Result;
}
export async function api<T>(route: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api/analysis/${route}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload as T;
}
