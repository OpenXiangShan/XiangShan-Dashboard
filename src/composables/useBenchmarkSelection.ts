import {
  getSpecGeomeanName,
  getSpecGroup,
  type SpecCategory,
  type SpecVersion,
} from "../config/spec";

export function selectDefault(benchmarks: string[]): string[] {
  const nonLegacy = benchmarks.filter((tc) => !tc.startsWith("legacy-"));
  return nonLegacy.length ? nonLegacy : [...benchmarks];
}

export function isSpecBenchmark(
  name: string,
  version: SpecVersion,
  category: SpecCategory,
): boolean {
  if (name === getSpecGeomeanName(version, category)) return true;

  const normalizedName = name.toLowerCase();
  const hasNumericPrefix = /^\d+\./.test(name);
  return getSpecGroup(version, category).benchmarks.some((benchmark) => {
    const candidate = hasNumericPrefix
      ? benchmark
      : benchmark.replace(/^\d+\./, "");
    if (normalizedName === candidate.toLowerCase()) return true;

    // SPEC rate data may omit the conventional `_r` suffix while retaining
    // the 5xx/7xx benchmark number. Keep the numeric prefix in the match.
    return (
      /^(?:5|7)\d{2}\./.test(benchmark) &&
      normalizedName === candidate.replace(/_r$/, "").toLowerCase()
    );
  });
}

export function selectSpecCategory(
  benchmarks: string[],
  version: SpecVersion,
  category: SpecCategory,
): string[] {
  return benchmarks.filter((name) => isSpecBenchmark(name, version, category));
}

export function toggleSelection(
  all: string[],
  selected: string[],
  name: string,
): string[] {
  const set = new Set(selected);
  if (set.has(name)) set.delete(name);
  else set.add(name);
  return all.filter((tc) => set.has(tc));
}
