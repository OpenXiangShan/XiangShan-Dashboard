export interface Commit {
  commit_sha: string;
  short_sha: string;
  commit_epoch: number;
  commit_time: string;
  subject: string;
}

export interface Metric {
  metric_id: string;
  display_name: string;
  category: string;
  unit: string;
  direction: string;
  semantic_version: string;
}

export interface DatasetEntry {
  id: string;
  profile: string;
  label: string;
  suite: string;
  run_count: number;
  commit_count: number;
}

export interface Catalog {
  schema_version: string;
  generated_at: string;
  commits: Commit[];
  metrics: Metric[];
  datasets: DatasetEntry[];
  default_dataset: string;
}

export interface Slice {
  slice_id: string;
  benchmark: string;
  workload: string;
  checkpoint: number;
  checkpoint_identity: string;
  weight: number;
  name: string;
}

export interface Score {
  object_id: string;
  value: number | null;
  status: string;
  minimum_coverage: number | null;
  source_uri: string;
}

export interface Workload {
  workload: string;
  weighted_ipc: number | null;
  status: string;
  valid_member_count: number;
  total_member_count: number;
}

export type ResultRow = [
  number,
  number | null,
  string,
  string | null,
  number | null,
  number,
];
export type CounterRow = [
  number,
  number,
  number | null,
  string,
  string | null,
  number | null,
  string,
];

export interface Run {
  run_id: string;
  commit_sha: string;
  config: string;
  snapshot_at: string;
  source_uri: string;
  status: string;
  comparison_key: string;
  slice_set_id: string;
  conditions_verified: boolean;
  comparison_status?: string;
  comparison_policy?: string;
  runtime_conditions: Record<string, unknown>;
  score_coverage: string | null;
  valid_slices: number;
  expected_slices: number;
  scores: Score[];
  workloads: Workload[];
  results: ResultRow[];
  counters: CounterRow[];
}

export type Observation = Run & Commit;
export interface Dataset {
  runs: Run[];
  slices: Slice[];
}
export interface Assessment {
  allowed: boolean;
  reasons: string[];
  notes: string[];
  mode: string;
}
export interface ValuePair extends Assessment {
  a: number | null;
  b: number | null;
  count: number;
  total: number;
}
export interface SliceResult {
  ipc: number | null;
  status: string;
  window_id: string | null;
  seed: number | null;
  weight: number;
}
export interface Counter {
  value: number | null;
  availability: string;
  window_id: string | null;
  semantic_version: string;
}
export type CounterUnit = "raw" | "per_kinst";
