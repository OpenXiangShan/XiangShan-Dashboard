import comparison from "@comparison";
import type {
  Assessment,
  Catalog,
  Counter,
  CounterUnit,
  Dataset,
  Metric,
  Observation,
  Score,
  Slice,
  SliceResult,
  ValuePair,
} from "../types/history";

export const BASE_METRIC = "rob_committed_instructions";
export const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export const percent = (a: number | null, b: number | null) =>
  finite(a) && finite(b)
    ? a === 0
      ? b === 0
        ? 0
        : null
      : (b / a - 1) * 100
    : null;

export async function loadJson<T>(root: string, name: string): Promise<T> {
  const response = await fetch(`${root.replace(/\/$/, "")}/${name}`);
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return response.json();
}

export function observationLabel(run: Observation): string {
  return `${run.short_sha} · ${run.commit_time.slice(5, 10)} · ${run.score_coverage || "范围未知"} · ${run.valid_slices}/${run.expected_slices} · ${run.status === "published" ? "完整" : run.status === "partial" ? "部分数据" : run.status} · ${run.run_id.slice(0, 12)}`;
}

export class History {
  readonly runs: Observation[];
  readonly slices: Slice[];
  readonly workloads: string[];
  private readonly sliceIndexes: Map<string, number>;
  private readonly metricIndexes: Map<string, number>;
  private readonly results = new WeakMap<
    Observation,
    Map<number, SliceResult>
  >();
  private readonly counters = new WeakMap<Observation, Map<number, Counter>>();

  constructor(
    readonly catalog: Catalog,
    dataset: Dataset,
  ) {
    const commits = new Map(
      catalog.commits.map((commit) => [commit.commit_sha, commit]),
    );
    this.runs = dataset.runs
      .map((run) => ({ ...commits.get(run.commit_sha)!, ...run }))
      .sort(
        (a, b) =>
          a.commit_epoch - b.commit_epoch ||
          a.snapshot_at.localeCompare(b.snapshot_at) ||
          a.run_id.localeCompare(b.run_id),
      );
    this.slices = dataset.slices;
    this.workloads = [
      ...new Set(this.slices.map((slice) => slice.workload)),
    ].sort();
    this.sliceIndexes = new Map(
      this.slices.map((slice, index) => [slice.slice_id, index]),
    );
    this.metricIndexes = new Map(
      catalog.metrics.map((metric, index) => [metric.metric_id, index]),
    );
  }

  result(run: Observation, slice: Slice): SliceResult | undefined {
    if (!this.results.has(run))
      this.results.set(
        run,
        new Map(
          run.results.map(([index, ipc, status, window_id, seed, weight]) => [
            index,
            { ipc, status, window_id, seed, weight },
          ]),
        ),
      );
    return this.results.get(run)!.get(this.sliceIndexes.get(slice.slice_id)!);
  }

  counter(
    run: Observation,
    slice: Slice,
    metricId: string,
  ): Counter | undefined {
    const width = this.catalog.metrics.length;
    if (!this.counters.has(run))
      this.counters.set(
        run,
        new Map(
          run.counters.map(
            ([
              sliceIndex,
              metricIndex,
              value,
              availability,
              window_id,
              ,
              semantic_version,
            ]) => [
              sliceIndex * width + metricIndex,
              { value, availability, window_id, semantic_version },
            ],
          ),
        ),
      );
    return this.counters
      .get(run)!
      .get(
        this.sliceIndexes.get(slice.slice_id)! * width +
          this.metricIndexes.get(metricId)!,
      );
  }

  counterValue(
    run: Observation,
    slice: Slice,
    metric: Metric,
    unit: CounterUnit,
  ): number | null {
    const row = this.counter(run, slice, metric.metric_id);
    if (row?.availability !== "available" || !finite(row.value)) return null;
    if (unit === "raw" || metric.metric_id === BASE_METRIC) return row.value;
    const base = this.counter(run, slice, BASE_METRIC);
    return base?.availability === "available" &&
      finite(base.value) &&
      base.value > 0 &&
      !!row.window_id &&
      row.window_id === base.window_id
      ? (row.value / base.value) * 1000
      : null;
  }

  slicePair(a: Observation, b: Observation, slice: Slice): Assessment {
    return comparison.slice(a, b, this.result(a, slice), this.result(b, slice));
  }

  counterPair(
    a: Observation,
    b: Observation,
    slice: Slice,
    metric: Metric,
    unit: CounterUnit,
  ): Assessment {
    return comparison.counter(
      a,
      b,
      this.result(a, slice),
      this.result(b, slice),
      this.counter(a, slice, metric.metric_id),
      this.counter(b, slice, metric.metric_id),
      this.counter(a, slice, BASE_METRIC),
      this.counter(b, slice, BASE_METRIC),
      unit === "per_kinst" && metric.metric_id !== BASE_METRIC,
    );
  }

  pairSlices(a: Observation, b: Observation, workload?: string): Slice[] {
    return this.slices.filter(
      (slice) =>
        (!workload || slice.workload === workload) &&
        (this.result(a, slice) || this.result(b, slice)),
    );
  }

  workloadPair(a: Observation, b: Observation, workload: string): ValuePair {
    const pair = comparison.runs(a, b);
    const left = a.workloads.find((row) => row.workload === workload);
    const right = b.workloads.find((row) => row.workload === workload);
    const slices = this.pairSlices(a, b, workload);
    const empty = {
      ...pair,
      a: left?.weighted_ipc ?? null,
      b: right?.weighted_ipc ?? null,
      count: 0,
      total: slices.length,
    };
    if (!pair.allowed) return empty;
    const complete = [left, right].every(
      (row) =>
        row?.status === "valid" &&
        finite(row.weighted_ipc) &&
        row.weighted_ipc > 0,
    );
    const sameScope = a.score_coverage === b.score_coverage;
    if (
      complete &&
      sameScope &&
      a.comparison_key === b.comparison_key &&
      a.slice_set_id === b.slice_set_id &&
      a.status === "published" &&
      b.status === "published"
    )
      return { ...empty, count: slices.length };
    const common = slices.filter((slice) => {
      const x = this.result(a, slice),
        y = this.result(b, slice);
      return (
        this.slicePair(a, b, slice).allowed &&
        finite(x?.weight) &&
        x.weight > 0 &&
        x.weight === y?.weight
      );
    });
    if (!common.length)
      return {
        ...empty,
        allowed: false,
        reasons: ["无共同有效且权重一致的切片"],
      };
    const weight = common.reduce(
      (sum, slice) => sum + this.result(a, slice)!.weight,
      0,
    );
    const ipc = (run: Observation) =>
      weight /
      common.reduce((sum, slice) => {
        const row = this.result(run, slice)!;
        return sum + row.weight / row.ipc!;
      }, 0);
    const full = complete && sameScope && common.length === slices.length;
    return {
      ...pair,
      a: full ? left!.weighted_ipc : ipc(a),
      b: full ? right!.weighted_ipc : ipc(b),
      count: common.length,
      total: slices.length,
      notes: [
        ...pair.notes,
        `共同 ${common.length}/${slices.length} 切片；两侧使用相同切片与权重`,
      ],
    };
  }

  score(run: Observation, name: string): Score | undefined {
    return run.scores.find(
      (row) =>
        row.object_id === name &&
        ["valid", "partial"].includes(row.status) &&
        finite(row.value),
    );
  }

  scorePair(a: Observation, b: Observation, name: string): Assessment {
    const pair = comparison.runs(a, b);
    const reasons = [...pair.reasons],
      notes = [...pair.notes];
    const x = this.score(a, name),
      y = this.score(b, name);
    if (![x?.value, y?.value].every((value) => finite(value) && value > 0))
      reasons.push("当前分数缺失");
    if (
      ![x?.minimum_coverage, y?.minimum_coverage].every(
        (value) => finite(value) && value > 0,
      )
    )
      reasons.push("评分覆盖率缺失");
    else if (x!.minimum_coverage! < 1 || y!.minimum_coverage! < 1) {
      const members = this.pairSlices(a, b).filter(
        (slice) => name.startsWith("SPEC") || slice.benchmark === name,
      );
      const same = members.every((slice) => {
        const left = this.result(a, slice),
          right = this.result(b, slice);
        const validA = left?.status === "valid",
          validB = right?.status === "valid";
        return (
          validA === validB &&
          (!validA || (finite(left?.weight) && left.weight === right?.weight))
        );
      });
      if (
        x!.minimum_coverage !== y!.minimum_coverage ||
        x!.source_uri.split("/").pop() !== y!.source_uri.split("/").pop() ||
        !same ||
        !members.some((slice) => this.result(a, slice)?.status === "valid")
      )
        reasons.push("评分覆盖范围不同");
      else
        notes.push(
          `相同切片范围；最低覆盖 ${(x!.minimum_coverage! * 100).toFixed(1)}%`,
        );
    }
    if (
      a.score_coverage !== b.score_coverage ||
      a.slice_set_id !== b.slice_set_id
    )
      reasons.push("评分切片集不同");
    return {
      allowed: !reasons.length,
      reasons,
      notes,
      mode: reasons.length ? "incomparable" : "diagnostic",
    };
  }
}
