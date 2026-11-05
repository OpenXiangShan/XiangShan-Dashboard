"use strict";

// Item comparisons use recorded conditions and exact slice IDs, independently of suite coverage.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Comparison = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const fields = {
    emulator: "仿真器",
    warmup: "预热规则",
    max_instr: "指令上限",
    max_cycles: "周期上限",
    dram_config: "DRAM 配置",
    cpu_frequency_mhz: "CPU 频率",
    dram_frequency_mhz: "DRAM 频率",
  };
  const known = (value) => value != null && value !== "" && value !== "unknown";
  const finish = (reasons, notes = []) => ({
    allowed: !reasons.length,
    reasons,
    notes,
    mode: reasons.length ? "incomparable" : "diagnostic",
  });

  function runs(a, b) {
    const reasons = [],
      notes = [];
    if (!a || !b) return finish(["未选择 A/B 测量"]);
    if (a.config !== b.config) reasons.push("硬件配置不同");
    for (const [label, run] of [
      ["A", a],
      ["B", b],
    ]) {
      if (!["published", "partial"].includes(run.status))
        reasons.push(`${label} 测量状态为 ${run.status}`);
      else if (run.status === "partial")
        notes.push(`${label} 为部分运行，逐项检查已有数据`);
    }
    const permits = (run) =>
      run.conditions_verified ||
      (run.comparison_status === "provisional" &&
        run.comparison_policy === "historical-diagnostic/v1");
    const sameGroup =
      a.comparison_key &&
      a.comparison_key === b.comparison_key &&
      a.slice_set_id === b.slice_set_id &&
      permits(a) &&
      permits(b);
    const ca = a.runtime_conditions || {},
      cb = b.runtime_conditions || {};
    for (const [key, label] of Object.entries(fields)) {
      if (known(ca[key]) && known(cb[key])) {
        if (String(ca[key]) !== String(cb[key]))
          reasons.push(`${label}不同：${ca[key]} / ${cb[key]}`);
      } else if (!sameGroup) reasons.push(`${label}缺少可核对记录`);
    }
    if (known(ca.build_options) && known(cb.build_options)) {
      if (ca.build_options !== cb.build_options) reasons.push("构建参数不同");
    } else notes.push("构建参数待核实");
    if (a.slice_set_id !== b.slice_set_id)
      notes.push("切片集不同，仅比较共同切片");
    notes.push("历史测量诊断；单次变化不代表统计显著性");
    return finish(reasons, notes);
  }

  function slice(a, b, left, right) {
    const pair = runs(a, b),
      reasons = [...pair.reasons],
      notes = [...pair.notes];
    for (const [label, row] of [
      ["A", left],
      ["B", right],
    ]) {
      if (!row) reasons.push(`${label} 未包含该切片身份`);
      else if (
        row.status !== "valid" ||
        !Number.isFinite(row.ipc) ||
        row.ipc <= 0
      )
        reasons.push(`${label} 切片数据无效或缺失（${row.status}）`);
    }
    if (left && right) {
      if (!left.window_id || !right.window_id) reasons.push("切片测量窗口缺失");
      else if (left.window_id !== right.window_id)
        reasons.push("切片测量窗口不同");
      if (left.seed !== right.seed) notes.push("随机种子不同");
    }
    return finish(reasons, notes);
  }

  function counter(
    a,
    b,
    leftSlice,
    rightSlice,
    left,
    right,
    denominatorA,
    denominatorB,
    perInstruction,
  ) {
    const pair = runs(a, b),
      reasons = [...pair.reasons],
      notes = [...pair.notes];
    for (const [label, row, member] of [
      ["A", left, leftSlice],
      ["B", right, rightSlice],
    ]) {
      if (!member) reasons.push(`${label} 未包含该切片身份`);
      if (
        !row ||
        row.availability !== "available" ||
        !Number.isFinite(row.value)
      )
        reasons.push(
          `${label} 计数器缺测（${row?.availability || "missing"}）`,
        );
    }
    if (left && right) {
      if (!left.window_id || !right.window_id) reasons.push("计数器窗口缺失");
      else if (left.window_id !== right.window_id)
        reasons.push("计数器窗口不同");
      if (!left.semantic_version || !right.semantic_version)
        reasons.push("计数器语义版本缺失");
      else if (left.semantic_version !== right.semantic_version)
        reasons.push("计数器语义版本不同");
    }
    if (perInstruction) {
      for (const [label, row, denominator] of [
        ["A", left, denominatorA],
        ["B", right, denominatorB],
      ]) {
        if (
          denominator?.availability !== "available" ||
          !Number.isFinite(denominator?.value) ||
          denominator.value <= 0
        )
          reasons.push(`${label} 缺少正的退休指令数`);
        else if (row?.window_id !== denominator.window_id)
          reasons.push(`${label} 计数器与退休指令窗口不同`);
      }
      if (
        denominatorA &&
        denominatorB &&
        denominatorA.semantic_version !== denominatorB.semantic_version
      )
        reasons.push("退休指令计数器语义版本不同");
    } else notes.push("原始计数差值未按退休指令数归一化");
    if (leftSlice && rightSlice && leftSlice.seed !== rightSlice.seed)
      notes.push("随机种子不同");
    return finish(reasons, notes);
  }
  return { runs, slice, counter };
});
