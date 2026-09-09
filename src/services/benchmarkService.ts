import {
  SPEC_BENCHMARK_GROUPS,
  SPEC_VERSIONS,
  type SpecVersion,
} from "../config/spec";
import type { ReportPayload } from "../types/data";

interface BenchmarkMatch {
  name: string;
  score: number;
}

function versionBenchmarks(version: SpecVersion): readonly string[] {
  const groups = SPEC_BENCHMARK_GROUPS[version];
  return [...groups.int.benchmarks, ...groups.fp.benchmarks];
}

function matchBenchmark(name: string, canonical: string): number {
  const normalizedName = name.toLowerCase();
  const normalizedCanonical = canonical.toLowerCase();
  if (normalizedName === normalizedCanonical) return 3;

  const withoutPrefix = normalizedCanonical.replace(/^\d+\./, "");
  if (normalizedName === withoutPrefix) return 2;

  // Some rate reports retain the numeric prefix but omit `_r`.
  if (
    /^\d+\./.test(name) &&
    withoutPrefix.endsWith("_r") &&
    normalizedName === normalizedCanonical.replace(/_r$/, "")
  ) {
    return 1;
  }
  return 0;
}

function bestMatch(name: string, version: SpecVersion): BenchmarkMatch | null {
  return versionBenchmarks(version).reduce<BenchmarkMatch | null>(
    (best, benchmark) => {
      const score = matchBenchmark(name, benchmark);
      return score > (best?.score || 0) ? { name: benchmark, score } : best;
    },
    null,
  );
}

export function detectSpecVersion(
  benchmarks: string[],
): SpecVersion | undefined {
  const matches = SPEC_VERSIONS.map((version) => ({
    version,
    score: benchmarks.reduce(
      (total, name) => total + (bestMatch(name, version)?.score || 0),
      0,
    ),
  }));
  const best = matches.reduce((current, item) =>
    item.score > current.score ? item : current,
  );
  return best.score ? best.version : undefined;
}

export function normalizeSpecBenchmarkName(
  name: string,
  version?: SpecVersion,
): string {
  // Numeric benchmark IDs uniquely identify their SPEC version.
  if (/^\d+\./.test(name)) {
    for (const candidateVersion of SPEC_VERSIONS) {
      const match = bestMatch(name, candidateVersion);
      if (match) return match.name;
    }
    return name;
  }

  return version ? bestMatch(name, version)?.name || name : name;
}

export function normalizeReportPayload(
  payload: ReportPayload,
  version = detectSpecVersion(Object.keys(payload)),
): ReportPayload {
  const normalized: ReportPayload = {};
  const priorities = new Map<string, number>();

  for (const [name, entry] of Object.entries(payload)) {
    const normalizedName = normalizeSpecBenchmarkName(name, version);
    const priority = /^\d+\./.test(name) ? 2 : 1;
    if ((priorities.get(normalizedName) || 0) > priority) continue;
    normalized[normalizedName] = entry;
    priorities.set(normalizedName, priority);
  }
  return normalized;
}
