import type { Row } from "./analysisApi";

export const TOPDOWN_METRICS = [
  "baseRetiring",
  "frontendBound",
  "badSpecBound",
  "backendBound",
];
export const TOPDOWN_LABELS = [
  "Retiring",
  "Frontend Bound",
  "Bad Speculation",
  "Backend Bound",
];
export const DEFAULT_METRICS = ["cpi", ...TOPDOWN_METRICS];

// Diagnose each side independently: a missing component is not a zero share.
export function topdownDiagnostics(rows: Row[], side: "a" | "b") {
  const names = [...new Set(rows.map((row) => row.name))];
  const index = new Map(rows.map((row) => [`${row.name}:${row.metric}`, row]));
  const missing = TOPDOWN_METRICS.map((metric, i) => {
    const count = names.filter((name) => {
      const value = index.get(`${name}:${metric}`)?.[side];
      return value == null || !Number.isFinite(value);
    }).length;
    return count ? `${TOPDOWN_LABELS[i]}（${count}/${names.length}）` : "";
  }).filter(Boolean);
  let invalid = 0;
  let unbalanced = 0;
  for (const name of names) {
    const values = TOPDOWN_METRICS.map(
      (metric) => index.get(`${name}:${metric}`)?.[side],
    );
    if (
      values.some(
        (value) =>
          value != null && Number.isFinite(value) && (value < 0 || value > 1),
      )
    )
      invalid++;
    if (
      values.every((value) => value != null && Number.isFinite(value)) &&
      Math.abs(values.reduce<number>((sum, value) => sum + value!, 0) - 1) >
        1e-6
    )
      unbalanced++;
  }
  const messages: string[] = [];
  if (missing.length)
    messages.push(
      `部分分解：当前结果缺少 ${missing.join("、")}；空白不代表 0。`,
    );
  if (invalid)
    messages.push(
      `${invalid} 项含负值或超过 100% 的分量，不能视为有效占比；图表与明细保留原值。`,
    );
  if (unbalanced)
    messages.push(`${unbalanced} 项的四项合计不等于 100%，请核对计数口径。`);
  return messages;
}
