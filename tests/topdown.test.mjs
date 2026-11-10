import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(
  new URL("../src/internal/topdown.ts", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
});
const { TOPDOWN_METRICS, DEFAULT_METRICS, topdownDiagnostics } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);
const rows = (a, b) =>
  TOPDOWN_METRICS.map((metric, i) => ({
    name: "gcc",
    metric,
    a: a[i],
    b: b[i],
  }));

test("initial query contains CPI and all four L1 components", () => {
  assert.deepEqual(DEFAULT_METRICS, ["cpi", ...TOPDOWN_METRICS]);
});
test("RTL missing speculation stays distinct from a measured GEM5 zero", () => {
  const data = rows([0.3, 0.1, null, null], [0.3, 0.1, 0, 0.6]);
  const message = topdownDiagnostics(data, "a").join(" ");
  assert.match(message, /Bad Speculation（1\/1）/);
  assert.match(message, /Backend Bound（1\/1）/);
  assert.deepEqual(topdownDiagnostics(data, "b"), []);
});
test("negative ratios and nonclosing totals are reported without changing values", () => {
  const data = rows([0.7, 0.1, -0.1, 0.3], [0.3, 0.1, 0.1, 0.4]);
  assert.match(topdownDiagnostics(data, "a").join(" "), /负值/);
  assert.match(topdownDiagnostics(data, "b").join(" "), /合计不等于 100%/);
  assert.equal(data[2].a, -0.1);
});
test("a filtered-out component is missing even if the other side has values", () => {
  const data = rows([0.3, 0.1, 0.1, 0.5], [0.3, 0.1, 0.1, 0.5]).slice(0, 3);
  assert.match(
    topdownDiagnostics(data, "a").join(" "),
    /Backend Bound（1\/1）/,
  );
});
