'use strict';

const $ = id => document.getElementById(id);
const DATA_ROOT = new URLSearchParams(location.search).get('data') || '../outputs/mainline-history';
const STABLE_THRESHOLD = 0.5;
const BASE_METRIC = 'rob_committed_instructions';
const PER_KINST = 1000;

const model = {
  manifest: null,
  commits: [],
  workloads: [],
  workloadRows: new Map(),
  slices: [],
  sliceIndex: new Map(),
  slicesByWorkload: new Map(),
  counterMetrics: [],
  counterMetricById: new Map(),
  counterSlices: [],
  counterSliceIndex: new Map(),
  counterValues: new Map(),
  sliceResults: new Map(),
  runSlices: new Map(),
  scoreValues: new Map(),
  scoreSources: new Map(),
  scoreCoverage: new Map(),
  anomalies: null
};

let state = {
  level: 'suite',
  workload: '',
  slice: '',
  metric: 'score',
  scoreSuite: '',
  base: 0,
  target: 0,
  mode: 'absolute',
  sort: 'impact',
  counterMetric: '',
  counterSlice: '',
  counterUnit: 'raw',
  relationMode: 'level',
  counterDisplay: 'combined',
  selectedCounterMetrics: [],
  workloadFilter: 'all',
  workloadSearch: ''
};

const number = value => value === '' || value == null ? null : Number(value);
const finite = value => Number.isFinite(value);
const signed = (value, digits = 2) => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '—';
const pct = (value, digits = 2) => finite(value) ? `${signed(value, digits)}%` : '—';
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const shortDate = value => new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit'}).format(new Date(value));
const fullDate = value => new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(value));
const tone = value => !finite(value) || Math.abs(value) <= STABLE_THRESHOLD ? 'neutral' : value > 0 ? 'good' : 'bad';
const coreMetricOptions = '<option value="index">归一化指数</option><option value="ipc">IPC</option><option value="cpi">CPI</option>';
const counterKey = (slice, metric, commit) => `${slice}\u0000${metric}\u0000${commit}`;
const formatCounterValue = value => finite(value) ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2}).format(value) : '—';

async function fetchText(path, optional = false) {
  const response = await fetch(`${DATA_ROOT}/${path}`);
  if (optional && response.status === 404) return 'null';
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.text();
}

async function loadData() {
  const catalog = JSON.parse(await fetchText('manifest.json'));
  if (!catalog.datasets.length) {
    $('loading').hidden = true;
    $('error').hidden = false;
    $('error').innerHTML = '<strong>暂无测量数据</strong><span>请导入运行后重新导出看板。</span>';
    return false;
  }
  const datasetId = new URLSearchParams(location.search).get('dataset') || catalog.default_dataset;
  const dataset = catalog.datasets.find(item => item.id === datasetId)
    || catalog.datasets.find(item => item.id === catalog.default_dataset);
  const [dataText, anomalyText] = await Promise.all([
    fetchText(`${dataset.id}.json`), fetchText('performance-anomalies.json', true)
  ]);
  const data = JSON.parse(dataText);
  model.manifest = {...catalog, ...dataset};
  model.commits = ObservationFilter.buildObservations(catalog.commits, data.runs);
  if (!model.commits.length) {
    $('loading').hidden = true;
    $('error').hidden = false;
    $('error').innerHTML = '<strong>暂无测量数据</strong><span>当前数据集没有测量记录。</span>';
    return false;
  }
  model.measuredCommits = ObservationFilter.measuredCatalogCommits(catalog.commits, model.commits);
  const runIds = new Set(model.commits.map(run => run.run_id));
  data.runs = data.runs.filter(run => runIds.has(run.run_id));
  model.slices = data.slices.map(row => ({...row, slice:row.slice_id}));
  model.sliceIndex = new Map(model.slices.map(row => [row.slice, row]));
  for (const row of model.slices) {
    if (!model.slicesByWorkload.has(row.workload)) model.slicesByWorkload.set(row.workload, []);
    model.slicesByWorkload.get(row.workload).push(row);
  }
  model.workloads = [...model.slicesByWorkload.keys()].sort((a, b) => a.localeCompare(b));
  const availableMetrics = new Set(), availableSlices = new Set();
  for (const run of data.runs) {
    model.runSlices.set(run.run_id, new Set());
    for (const [sliceIndex, ipc, status, window_id, seed, weight] of run.results) {
      const slice = model.slices[sliceIndex];
      slice[`ipc_${run.run_id}`] = ipc;
      model.sliceResults.set(counterKey(slice.slice, 'result', run.run_id), {ipc,status,window_id,seed,weight});
      model.runSlices.get(run.run_id).add(slice.slice);
    }
    for (const row of run.workloads) {
      if (!model.workloadRows.has(row.workload)) model.workloadRows.set(row.workload, []);
      model.workloadRows.get(row.workload).push({...row, observation_key:run.run_id,
        weighted_ipc:row.status === 'valid' ? row.weighted_ipc : null,
        status:row.status === 'valid' ? 'complete' : 'partial',
        slice_count:row.valid_member_count});
    }
    for (const row of run.scores) {
      if (['valid', 'partial'].includes(row.status) && finite(row.value)) {
        const key = `${row.object_id}\u0000${run.run_id}`;
        model.scoreValues.set(key, row.value);
        model.scoreSources.set(key, row.source_uri.split('/').at(-1));
        model.scoreCoverage.set(key, row.minimum_coverage);
      }
    }
    for (const [sliceIndex, metricIndex, value, availability, window_id, dump_time, semantic_version] of run.counters) {
      model.counterValues.set(counterKey(model.slices[sliceIndex].slice,
        catalog.metrics[metricIndex].metric_id, run.run_id), {value, availability, window_id, dump_time, semantic_version});
      if (availability === 'available' && finite(value)) {
        availableMetrics.add(catalog.metrics[metricIndex].metric_id);
        availableSlices.add(model.slices[sliceIndex].slice);
      }
    }
  }
  model.counterMetrics = catalog.metrics.filter(metric => availableMetrics.has(metric.metric_id));
  model.counterMetricById = new Map(model.counterMetrics.map(metric => [metric.metric_id, metric]));
  model.counterSlices = model.slices.filter(slice => availableSlices.has(slice.slice));
  model.counterSliceIndex = new Map(model.counterSlices.map(slice => [slice.slice, slice]));
  $('counterLevel').disabled = !model.counterMetrics.length;
  $('counterNav').hidden = !model.counterMetrics.length;
  model.anomalies = JSON.parse(anomalyText);
  if (model.anomalies) {
    model.anomalies.comparisons = (model.anomalies.comparisons || []).filter(row =>
      runIds.has(row.baseline.run_id) && runIds.has(row.current.run_id));
  }
  state.scoreSuite = dataset.suite;
  state.workload = model.workloads.includes('mcf') ? 'mcf' : model.workloads[0];
  state.slice = model.slicesByWorkload.get(state.workload)?.[0]?.slice || '';
  state.counterSlice = model.counterSlices.find(slice => slice.workload === state.workload)?.slice
    || model.counterSlices[0]?.slice || '';
  state.counterMetric = model.counterMetricById.has('branch_mispredictions') ? 'branch_mispredictions' : model.counterMetrics[0]?.metric_id || '';
  state.selectedCounterMetrics = [state.counterMetric];
  Object.assign(state, defaultComparison());
  const candidates = model.slicesByWorkload.get(state.workload) || [];
  const selected = candidates.find(row => sliceAssessment(row.slice).allowed)
    || candidates.find(row => finite(sliceIPC(row, state.target))) || candidates[0];
  if (selected) state.slice = selected.slice;
  state.counterSlice = candidates.find(row => counterAssessment(row.slice).allowed)?.slice
    || candidates.find(row => finite(counterRawValue(row.slice, state.target)))?.slice || state.counterSlice;
  $('dataset').innerHTML = catalog.datasets.map(row => `<option value="${row.id}">${escapeHtml(row.label)} · ${row.run_count} 次测量</option>`).join('');
  $('dataset').value = dataset.id;
}

function defaultComparison() {
  const measured = model.commits.map((run, index) => run.valid_slices > 0 ? index : -1)
    .filter(index => index >= 0);
  const target = measured.at(-1) ?? model.commits.length - 1;
  const targetRun = model.commits[target];
  for (let base = target - 1; base >= 0; base--) {
    if (model.commits[base].commit_sha === targetRun.commit_sha || !Comparison.runs(model.commits[base], targetRun).allowed) continue;
    if ([...(model.runSlices.get(targetRun.run_id) || [])].some(slice => sliceAssessment(slice, base, target).allowed))
      return {base, target};
  }
  return {base:measured[0] ?? 0, target};
}

function canCompare(fromIndex = state.base, toIndex = state.target) {
  const a = model.commits[fromIndex], b = model.commits[toIndex];
  const permits = run => run?.conditions_verified ||
    (run?.comparison_status === 'provisional' && run?.comparison_policy === 'historical-diagnostic/v1');
  return permits(a) && permits(b) && a.status === 'published' && b.status === 'published' &&
    a.comparison_key === b.comparison_key && a.slice_set_id === b.slice_set_id;
}

function sliceResult(slice, index) {
  return model.sliceResults.get(counterKey(slice, 'result', model.commits[index]?.run_id));
}

function sliceAssessment(slice, from = state.base, to = state.target) {
  return Comparison.slice(model.commits[from], model.commits[to], sliceResult(slice, from), sliceResult(slice, to));
}

function workloadAssessment(workload, from = state.base, to = state.target) {
  const rows = [workloadRow(workload, from), workloadRow(workload, to)];
  const complete = rows.every(row => row?.status === 'complete' && finite(row.weighted_ipc) && row.weighted_ipc > 0);
  const sameScope = model.commits[from].score_coverage === model.commits[to].score_coverage;
  const slices = pairSlices(model.slicesByWorkload.get(workload) || [], from, to);
  const pair = Comparison.runs(model.commits[from], model.commits[to]);
  const empty = {...pair, a:rows[0]?.weighted_ipc ?? null, b:rows[1]?.weighted_ipc ?? null,
    count:0, total:slices.length, common:true};
  if (!pair.allowed) return empty;
  if (complete && sameScope && canCompare(from, to))
    return {...pair, a:rows[0].weighted_ipc, b:rows[1].weighted_ipc,
      count:slices.length, total:slices.length, common:false};
  const common = slices.filter(slice => {
    const a = sliceResult(slice.slice, from), b = sliceResult(slice.slice, to);
    return sliceAssessment(slice.slice, from, to).allowed && finite(a.weight) && a.weight > 0 && a.weight === b.weight;
  });
  if (!common.length) return {...empty, allowed:false, mode:'incomparable', reasons:['无共同有效且权重一致的切片']};
  const full = complete && sameScope && common.length === slices.length;
  const weight = common.reduce((sum, slice) => sum + sliceResult(slice.slice, from).weight, 0);
  const ipc = index => weight / common.reduce((sum, slice) => {
    const row = sliceResult(slice.slice, index);
    return sum + row.weight / row.ipc;
  }, 0);
  return {...pair, a:full ? rows[0].weighted_ipc : ipc(from), b:full ? rows[1].weighted_ipc : ipc(to),
    count:common.length, total:slices.length, common:!full,
    notes:[...pair.notes, `共同 ${common.length}/${slices.length} 切片；两侧使用同一组切片与权重`]};
}

function workloadChange(workload, from = state.base, to = state.target) {
  const pair = workloadAssessment(workload, from, to);
  return pair.allowed ? (pair.b / pair.a - 1) * 100 : null;
}

function counterAssessment(slice, from = state.base, to = state.target, metricId = state.counterMetric) {
  return Comparison.counter(model.commits[from], model.commits[to], sliceResult(slice, from), sliceResult(slice, to),
    counterRow(slice, from, metricId), counterRow(slice, to, metricId),
    counterRow(slice, from, BASE_METRIC), counterRow(slice, to, BASE_METRIC),
    state.counterUnit === 'per_kinst' && metricId !== BASE_METRIC);
}

function pairSlices(slices = model.slices, from = state.base, to = state.target) {
  const a = model.runSlices.get(model.commits[from]?.run_id), b = model.runSlices.get(model.commits[to]?.run_id);
  return slices.filter(slice => a?.has(slice.slice) || b?.has(slice.slice));
}

function comparisonLabel(result, delta) {
  const text = result.allowed ? `诊断可比 · ${!finite(delta) ? '百分比未定义' : delta > STABLE_THRESHOLD ? '提升' : delta < -STABLE_THRESHOLD ? '退化' : '稳定'}`
    : result.reasons.join('；');
  return `<span class="status ${tone(delta)}" title="${escapeHtml([...result.reasons,...result.notes].join('；'))}">${escapeHtml(text)}</span>`;
}

function sliceImpact(slice, from = state.base, to = state.target) {
  if (!sliceAssessment(slice, from, to).allowed) return null;
  const a = sliceResult(slice, from), b = sliceResult(slice, to);
  return finite(a.weight) && a.weight === b.weight ? a.weight * (1 / b.ipc - 1 / a.ipc) : null;
}

function counterPerformanceChange(slice, from = state.base, to = state.target, metricId = state.counterMetric) {
  if (!counterAssessment(slice, from, to, metricId).allowed) return null;
  const a = counterValue(slice, from, metricId), b = counterValue(slice, to, metricId);
  if (!finite(a) || !finite(b)) return null;
  if (a === 0 && b === 0) return 0;
  return model.counterMetricById.get(metricId)?.direction === 'lower_is_better'
    ? (b !== 0 ? (a / b - 1) * 100 : null) : (a !== 0 ? (b / a - 1) * 100 : null);
}

function sliceName(slice) {
  return model.sliceIndex.get(slice)?.name || slice;
}

function observationLabel(commit) {
  if (!commit?.run_id) return `${commit?.short_commit || '—'} · 无测量`;
  const index = model.commits.indexOf(commit);
  const counterNote = state.level === 'counter' && !finite(counterValue(state.counterSlice, index))
    ? ' · 当前计数器缺测' : '';
  const ci = commit.actions_run_number ? `Weekly #${commit.actions_run_number} · ` : '';
  const coverage = model.scoreCoverage.get(`${state.scoreSuite}\u0000${commit.run_id}`);
  const scoreNote = state.level === 'suite' && state.metric === 'score' && finite(coverage)
    ? ` · 报告最低覆盖 ${(coverage * 100).toFixed(1)}%` : '';
  const scope = commit.score_coverage?.replace(/c$/, 'cov');
  const completeness = commit.status === 'partial' ? '部分数据' : commit.status === 'published' ? '完整' : commit.status;
  return `${ci}${commit.short_commit} · ${shortDate(commit.commit_time)} · ${commit.run_id.slice(0, 8)} · ${scope ? scope + ' · ' : ''}${commit.valid_slices}/${commit.expected_slices} ROI · ${completeness}${scoreNote}${counterNote}`;
}

function suiteScore(commitIndex) {
  return model.scoreValues.get(`${state.scoreSuite}\u0000${model.commits[commitIndex].observation_key}`) ?? null;
}

function scoreAssessment(from = state.base, to = state.target) {
  const a = model.commits[from], b = model.commits[to];
  const pair = Comparison.runs(a, b), reasons = [...pair.reasons], notes = [...pair.notes];
  const coverage = [a, b].map(run => model.scoreCoverage.get(`${state.scoreSuite}\u0000${run.run_id}`));
  if (![suiteScore(from), suiteScore(to)].every(value => finite(value) && value > 0))
    reasons.push('当前分数缺失');
  if (!coverage.every(value => finite(value) && value > 0)) reasons.push('评分覆盖率缺失');
  else if (coverage.some(value => value < 1)) {
    const sources = [a, b].map(run => model.scoreSources.get(`${state.scoreSuite}\u0000${run.run_id}`));
    const members = state.scoreSuite.startsWith('SPEC') ? model.slices
      : model.slices.filter(slice => slice.benchmark === state.scoreSuite);
    const slices = pairSlices(members, from, to);
    const sameMembers = slices.every(slice => {
      const left = sliceResult(slice.slice, from), right = sliceResult(slice.slice, to);
      const validA = left?.status === 'valid', validB = right?.status === 'valid';
      return validA === validB && (!validA || (finite(left.weight) && left.weight === right.weight));
    });
    if (coverage[0] !== coverage[1] || sources[0] !== sources[1] || !sameMembers ||
        !slices.some(slice => sliceResult(slice.slice, from)?.status === 'valid'))
      reasons.push('评分覆盖范围不同');
    else notes.push(`相同切片范围的评分 · 最低覆盖 ${(coverage[0] * 100).toFixed(1)}%`);
  }
  if (a.slice_set_id !== b.slice_set_id) reasons.push('评分切片集不同');
  return {allowed:!reasons.length, reasons, notes, mode:reasons.length ? 'incomparable' : 'diagnostic'};
}

function scoreChange(from = state.base, to = state.target) {
  return scoreAssessment(from, to).allowed ? (suiteScore(to) / suiteScore(from) - 1) * 100 : null;
}

function workloadRow(workload, commitIndex) {
  return model.workloadRows.get(workload)?.find(row => row.observation_key === model.commits[commitIndex].observation_key) || null;
}

function workloadIPC(workload, commitIndex) {
  return workloadRow(workload, commitIndex)?.weighted_ipc ?? null;
}

function sliceIPC(slice, commitIndex) {
  const value = number(slice?.[`ipc_${model.commits[commitIndex].observation_key}`]);
  return finite(value) ? value : null;
}

function selectedCounterMetric() {
  if (!state.counterMetric && state.selectedCounterMetrics?.length) state.counterMetric = state.selectedCounterMetrics[0];
  return model.counterMetricById.get(state.counterMetric) || model.counterMetrics[0];
}

function selectedCounterMetricIds() {
  return state.selectedCounterMetrics.filter(metricId => model.counterMetricById.has(metricId));
}

function counterRow(slice, commitIndex, metricId = state.counterMetric) {
  return model.counterValues.get(counterKey(slice, metricId, model.commits[commitIndex].observation_key)) || null;
}

function counterRawValue(slice, commitIndex, metricId = state.counterMetric) {
  const row = counterRow(slice, commitIndex, metricId);
  return row?.availability === 'available' && finite(row.value) ? row.value : null;
}

// 计数器和 IPC summary 属于不同窗口，因此归一化只用同窗口的 committed instructions。
// 归一化分母必须来自同一 PERF 窗口。
function counterValue(slice, commitIndex, metricId = state.counterMetric) {
  const raw = counterRawValue(slice, commitIndex, metricId);
  if (!finite(raw)) return null;
  if (state.counterUnit !== 'per_kinst' || metricId === BASE_METRIC) return raw;
  const base = counterRawValue(slice, commitIndex, BASE_METRIC);
  return finite(base) && base > 0 && counterRow(slice, commitIndex, metricId)?.window_id === counterRow(slice, commitIndex, BASE_METRIC)?.window_id ? raw / base * PER_KINST : null;
}

function counterUnitLabel(metric = selectedCounterMetric()) {
  if (state.counterUnit !== 'per_kinst' || metric.metric_id === BASE_METRIC) return metric.unit;
  return `${metric.unit}/KInst`;
}

function sliceLabel(slice) {
  const row = model.counterSliceIndex.get(slice);
  return row ? String(row.checkpoint) : slice;
}

function currentCounterWorkload() {
  return model.counterSliceIndex.get(state.counterSlice)?.workload || '';
}

function counterAnalysisSlices() {
  const workload = currentCounterWorkload();
  return pairSlices(workload ? model.counterSlices.filter(slice => slice.workload === workload) : model.counterSlices);
}

function ipcAt(slice, commitIndex) {
  const row = model.sliceIndex.get(slice);
  return row ? sliceIPC(row, commitIndex) : null;
}

function counterChangePct(slice, fromIndex, toIndex, metricId = state.counterMetric) {
  if (!counterAssessment(slice, fromIndex, toIndex, metricId).allowed) return null;
  const a = counterValue(slice, fromIndex, metricId), b = counterValue(slice, toIndex, metricId);
  return finite(a) && a !== 0 && finite(b) ? (b / a - 1) * 100 : null;
}

function ipcChangePct(slice, fromIndex, toIndex) {
  if (!sliceAssessment(slice, fromIndex, toIndex).allowed) return null;
  const a = ipcAt(slice, fromIndex), b = ipcAt(slice, toIndex);
  return (b / a - 1) * 100;
}

function rankAverage(values) {
  const order = values.map((value, index) => [value, index]).sort((a, b) => a[0] - b[0]);
  const ranks = new Array(values.length);
  let cursor = 0;
  while (cursor < order.length) {
    let end = cursor;
    while (end + 1 < order.length && order[end + 1][0] === order[cursor][0]) end++;
    const average = (cursor + end) / 2 + 1;
    for (let index = cursor; index <= end; index++) ranks[order[index][1]] = average;
    cursor = end + 1;
  }
  return ranks;
}

function pearson(xs, ys) {
  const n = xs.length;
  if (n < 2) return null;
  const meanX = xs.reduce((sum, value) => sum + value, 0) / n;
  const meanY = ys.reduce((sum, value) => sum + value, 0) / n;
  let cov = 0, varX = 0, varY = 0;
  for (let index = 0; index < n; index++) {
    const dx = xs[index] - meanX, dy = ys[index] - meanY;
    cov += dx * dy; varX += dx * dx; varY += dy * dy;
  }
  const denominator = Math.sqrt(varX * varY);
  return denominator > 0 ? cov / denominator : null;
}

function spearman(pairs) {
  const usable = pairs.filter(pair => finite(pair.x) && finite(pair.y));
  if (usable.length < 4) return {rho:null, n:usable.length};
  const rho = pearson(rankAverage(usable.map(pair => pair.x)), rankAverage(usable.map(pair => pair.y)));
  return {rho, n:usable.length};
}

function correlationStrength(rho) {
  const magnitude = Math.abs(rho);
  if (!finite(rho)) return '样本不足';
  if (magnitude >= 0.6) return '强';
  if (magnitude >= 0.4) return '中等';
  if (magnitude >= 0.2) return '弱';
  return '几乎无';
}

function relationPairs(mode, metricId = state.counterMetric) {
  const slices = counterAnalysisSlices();
  if (mode === 'panel') {
    const pairs = [];
    for (let index = 0; index < model.commits.length - 1; index++) {
      for (const slice of slices) {
        pairs.push({
          slice: slice.slice, weight: slice.weight,
          x: counterChangePct(slice.slice, index, index + 1, metricId),
          y: ipcChangePct(slice.slice, index, index + 1)
        });
      }
    }
    return pairs;
  }
  if (mode === 'delta') {
    return slices.map(slice => ({
      slice: slice.slice, weight: slice.weight,
      x: counterChangePct(slice.slice, state.base, state.target, metricId),
      y: ipcChangePct(slice.slice, state.base, state.target)
    }));
  }
  return slices.map(slice => ({
    slice: slice.slice, weight: slice.weight,
    x: counterValue(slice.slice, state.target, metricId),
    y: ipcAt(slice.slice, state.target)
  }));
}

function relationAxisLabels(mode, metric = selectedCounterMetric()) {
  if (mode === 'level') return {x:`${metric.display_name} · ${counterUnitLabel(metric)}`, y:'切片 IPC', xPct:false, yPct:false};
  return {x:`${metric.display_name} · 相对变化`, y:'IPC 相对变化', xPct:true, yPct:true};
}

function relationCaption(mode) {
  const workload = currentCounterWorkload();
  if (mode === 'level') return `每个点是一个 ${workload} 切片在 ${model.commits[state.target].short_commit} 的绝对水平；回答“哪些计数器解释了切片之间的 IPC 差异”。`;
  if (mode === 'delta') return `每个点是一个 ${workload} 切片从 ${model.commits[state.base].short_commit} 到 ${model.commits[state.target].short_commit} 的相对变化；回答“哪些计数器跟着 IPC 一起动”。`;
  return `每个点是“相邻 commit × ${workload} 切片”的变化对，汇总全部区间；样本更多但混合了不同 commit 的改动。`;
}

function suiteRatio(fromIndex, toIndex) {
  const ratios = comparisonData(fromIndex, toIndex).filter(row => finite(row.delta)).map(row => row.b / row.a);
  return ratios.length ? Math.exp(ratios.reduce((sum, ratio) => sum + Math.log(ratio), 0) / ratios.length) : null;
}

function absoluteValues() {
  const first = state.base;
  if (state.level === 'suite') {
    if (state.metric === 'score') return model.commits.map((_, index) => suiteScore(index));
    return model.commits.map((_, index) => { const ratio = suiteRatio(first, index); return finite(ratio) ? ratio * 100 : null; });
  }
  if (state.level === 'workload') {
    const values = model.commits.map((_, index) => workloadIPC(state.workload, index));
    if (state.metric === 'ipc') return values;
    if (state.metric === 'cpi') return values.map(value => finite(value) && value !== 0 ? 1 / value : null);
    return model.commits.map((_, index) => {
      const pair = workloadAssessment(state.workload, first, index);
      return pair.allowed ? pair.b / pair.a * 100 : null;
    });
  }
  if (state.level === 'counter') {
    return model.commits.map((_, index) => counterValue(state.counterSlice, index));
  }
  const slice = model.slices.find(item => item.slice === state.slice);
  const values = model.commits.map((_, index) => sliceIPC(slice, index));
  if (state.metric === 'ipc') return values;
  if (state.metric === 'cpi') return values.map(value => finite(value) && value !== 0 ? 1 / value : null);
  return values.map((value, index) => sliceAssessment(state.slice, first, index).allowed && finite(value) && finite(values[first]) ? value / values[first] * 100 : null);
}

function displayValues(values) {
  if (state.mode === 'absolute') return values;
  const baseline = values[state.base];
  return values.map((value, index) => {
    if (state.level === 'counter') return counterPerformanceChange(state.counterSlice, state.base, index);
    if (state.level === 'workload') return workloadChange(state.workload, state.base, index);
    const allowed = state.level === 'slice' ? sliceAssessment(state.slice, state.base, index).allowed
      : state.metric === 'score' ? scoreAssessment(state.base, index).allowed : finite(value);
    if (!allowed || !finite(value) || !finite(baseline) || baseline === 0) return null;
    return state.metric === 'cpi' ? (baseline / value - 1) * 100 : (value / baseline - 1) * 100;
  });
}

function currentName() {
  if (state.level === 'suite') return state.metric === 'score' ? `${state.scoreSuite}/GHz` : `${model.manifest.suite} · ${model.workloads.length} workloads`;
  if (state.level === 'workload') return state.workload;
  if (state.level === 'counter') return sliceName(state.counterSlice);
  return sliceName(state.slice);
}

function metricLabel() {
  if (state.level === 'counter') {
    const metric = selectedCounterMetric();
    return state.mode === 'relative' ? `${metric.display_name} · 性能变化` : `${metric.display_name} · ${counterUnitLabel(metric)}`;
  }
  if (state.mode === 'relative') return state.metric === 'score' ? '相对分数变化' : '相对 IPC 变化';
  return state.metric === 'score' ? '官方分数' : state.metric === 'ipc' ? 'IPC' : state.metric === 'cpi' ? 'CPI' : '归一化指数';
}

function syncControls() {
  if ((state.level === 'workload' || state.level === 'slice') && state.metric === 'score') state.metric = 'ipc';
  $('entityLabel').hidden = state.level === 'suite';
  $('scoreSuiteLabel').hidden = state.level !== 'suite' || state.metric !== 'score' || !model.manifest.score_suites.length;
  $('scoreSuite').innerHTML = [['套件', name => name.startsWith('SPEC')],
    ['Benchmark', name => !name.startsWith('SPEC')]].map(([label, includes]) =>
      `<optgroup label="${label}">${model.manifest.score_suites.filter(includes).map(name =>
        `<option value="${escapeHtml(name)}">${escapeHtml(name)}/GHz</option>`).join('')}</optgroup>`).join('');
  $('scoreSuite').value = state.scoreSuite;
  $('level').value = state.level;
  $('base').innerHTML = model.commits.map((commit, index) => `<option value="${index}">${escapeHtml(observationLabel(commit))}</option>`).join('');
  $('target').innerHTML = $('base').innerHTML;
  $('base').value = state.base;
  $('target').value = state.target;
  $('sort').value = state.sort;

  if (state.level === 'suite') {
    $('entity').innerHTML = `<option>${model.manifest.suite} · ${model.workloads.length} workloads</option>`;
    $('entity').disabled = true;
  } else if (state.level === 'workload') {
    $('entity').disabled = false;
    $('entity').innerHTML = model.workloads.map(workload => `<option value="${escapeHtml(workload)}">${escapeHtml(workload)}</option>`).join('');
    $('entity').value = state.workload;
  } else if (state.level === 'slice') {
    $('entity').disabled = false;
    $('entity').innerHTML = model.slices.map(slice => `<option value="${escapeHtml(slice.slice)}">${escapeHtml(slice.workload)} / ${escapeHtml(slice.checkpoint)}</option>`).join('');
    $('entity').value = state.slice;
  } else {
    $('entity').disabled = false;
    const groups = new Map();
    for (const slice of model.counterSlices) {
      if (!groups.has(slice.workload)) groups.set(slice.workload, []);
      groups.get(slice.workload).push(slice);
    }
    $('entity').innerHTML = [...groups].map(([workload, slices]) => `<optgroup label="${escapeHtml(workload)}">${slices.map(slice => `<option value="${escapeHtml(slice.slice)}">${escapeHtml(String(slice.checkpoint))} · ${escapeHtml(sliceName(slice.slice))} · ${counterCoverage(slice.slice, state.counterMetric)}/${model.commits.length} 测量</option>`).join('')}</optgroup>`).join('');
    $('entity').value = state.counterSlice;
  }

  if (state.level === 'counter') {
    const categories = new Map();
    for (const metric of model.counterMetrics) {
      if (!categories.has(metric.category)) categories.set(metric.category, []);
      categories.get(metric.category).push(metric);
    }
    $('metric').innerHTML = [...categories].map(([category, metrics]) => `<optgroup label="${escapeHtml(category)}">${metrics.map(metric => `<option value="${escapeHtml(metric.metric_id)}">${escapeHtml(metric.display_name)}</option>`).join('')}</optgroup>`).join('');
    $('metric').value = state.counterMetric;
    $('counterUnitLabel').hidden = false;
    $('counterUnit').value = state.counterUnit;
    renderCounterTrendControls();
  } else {
    $('metric').innerHTML = state.level === 'suite'
      ? `<option value="score">${model.manifest.suite}/GHz 分数</option><option value="index">IPC 归一化指数</option>`
      : coreMetricOptions;
    $('metric').value = state.metric;
    $('counterUnitLabel').hidden = true;
    $('counterTrendControls').hidden = true;
  }
  document.querySelectorAll('#relationMode [data-rel]').forEach(button => button.classList.toggle('selected', button.dataset.rel === state.relationMode));
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected', button.dataset.mode === state.mode));
}

function counterCoverage(slice, metricId) {
  return model.commits.reduce((count, _, index) => count + Number(finite(counterValue(slice, index, metricId))), 0);
}

function rawTrendCompatible(from, to) {
  return model.commits[from].config === model.commits[to].config;
}

function renderCounterTrendControls() {
  const ids = selectedCounterMetricIds();
  state.selectedCounterMetrics = ids;
  $('counterTrendControls').hidden = state.level !== 'counter';
  $('counterSelectionSummary').textContent = `${ids.length} / ${model.counterMetrics.length}`;
  const covered = counterCoverage(state.counterSlice, state.counterMetric);
  $('counterCoverage').textContent = `当前指标覆盖 ${covered} / ${model.commits.length} 次测量`;
  $('counterCoverage').title = '缺测不补零；趋势只显示实际观测。';
  document.querySelectorAll('#counterDisplayMode [data-display]').forEach(button => button.classList.toggle('selected', button.dataset.display === state.counterDisplay));
  const categories = new Map();
  for (const metric of model.counterMetrics) {
    if (!categories.has(metric.category)) categories.set(metric.category, []);
    categories.get(metric.category).push(metric);
  }
  $('counterMetricChecks').innerHTML = [...categories].map(([category, metrics]) => `<div class="counter-check-group"><strong>${escapeHtml(category)}</strong>${metrics.map(metric => {
    const checked = ids.includes(metric.metric_id) ? ' checked' : '';
    const focused = metric.metric_id === state.counterMetric ? ' focused' : '';
    const coverage = counterCoverage(state.counterSlice, metric.metric_id);
    return `<label class="counter-check${focused}" title="${escapeHtml(metric.display_name)}"><input type="checkbox" data-counter-check="${escapeHtml(metric.metric_id)}"${checked}><span>${escapeHtml(metric.display_name)} · ${coverage}/${model.commits.length}</span></label>`;
  }).join('')}</div>`).join('');
  $('counterMetricChecks').querySelectorAll('[data-counter-check]').forEach(input => {
    input.onchange = () => {
      const next = [...$('counterMetricChecks').querySelectorAll('[data-counter-check]:checked')].map(item => item.dataset.counterCheck);
      state.selectedCounterMetrics = next;
      if (next.length) state.counterMetric = next.includes(state.counterMetric) ? state.counterMetric : next[0];
      render();
    };
    input.parentElement.ondblclick = event => {
      event.preventDefault();
      state.counterMetric = input.dataset.counterCheck;
      render();
    };
  });
}

function drawChart(id, values, {color = '#2f63db', format = value => value.toFixed(2), connect = canCompare} = {}) {
  const valid = values.filter(finite);
  if (!valid.length) { $(id).innerHTML = '<div class="empty-state">当前筛选没有可用数据</div>'; return; }
  const W = 1120, H = 274, pad = {l:68, r:28, t:28, b:50}, plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  let low = Math.min(...valid), high = Math.max(...valid), spread = high - low;
  if (spread < 1e-8) spread = Math.max(Math.abs(high) * 0.04, 0.04);
  low -= spread * 0.2; high += spread * 0.2;
  const indices = values.map((_, index) => index);
  const observed = indices.filter(index => finite(values[index]));
  const x = index => pad.l + indices.indexOf(index) * plotWidth / Math.max(1, indices.length - 1);
  const y = value => pad.t + (high - value) / (high - low) * plotHeight;
  const segments = [];
  let segment = [];
  observed.forEach((index, position) => {
    if (segment.length && !connect(observed[position - 1], index)) { segments.push(segment); segment = []; }
    segment.push(`${x(index)},${y(values[index])}`);
  });
  if (segment.length) segments.push(segment);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(currentName())} 性能趋势">`;
  for (let step = 0; step <= 4; step++) {
    const value = low + (high - low) * step / 4;
    svg += `<line x1="${pad.l}" y1="${y(value)}" x2="${W - pad.r}" y2="${y(value)}" stroke="#e9eef5"/><text x="${pad.l - 13}" y="${y(value) + 4}" text-anchor="end" fill="#8795a9" font-size="11">${escapeHtml(format(value))}</text>`;
  }
  if (state.mode === 'relative' && low < 0 && high > 0) svg += `<line x1="${pad.l}" y1="${y(0)}" x2="${W - pad.r}" y2="${y(0)}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;
  for (const [index, letter, lineColor] of [[state.base, 'A', '#7d8ba0'], [state.target, 'B', color]]) {
    if (!indices.includes(index)) continue;
    svg += `<line x1="${x(index)}" y1="${pad.t}" x2="${x(index)}" y2="${H - pad.b}" stroke="${lineColor}" stroke-dasharray="4 5" opacity=".55"/><text x="${x(index)}" y="16" text-anchor="middle" fill="${lineColor}" font-size="11" font-weight="700">${letter}</text>`;
  }
  segments.filter(points => points.length > 1).forEach(points => { svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linejoin="round"/>`; });
  indices.forEach((index, position) => {
    const value = values[index];
    const commit = model.commits[index];
    if (position % Math.ceil(indices.length / 10) === 0 || index === state.base || index === state.target || position === indices.length - 1) svg += `<text x="${x(index)}" y="${H - 18}" text-anchor="middle" fill="${index === state.target ? color : '#7f8da1'}" font-size="11" font-weight="${index === state.target ? 700 : 500}">${commit.short_commit.slice(0, 7)}</text>`;
    if (finite(value)) svg += `<circle class="point" data-rev="${index}" cx="${x(index)}" cy="${y(value)}" r="${index === state.target ? 5.5 : 4}" fill="white" stroke="${color}" stroke-width="2.3" tabindex="0" role="button"><title>${escapeHtml(observationLabel(commit))} · ${format(value)}</title></circle>`;
  });
  for (const index of indices.filter(index => !finite(values[index]))) svg += `<text class="missing-point" data-rev="${index}" x="${x(index)}" y="${H - pad.b}" text-anchor="middle" fill="#8795a9" style="cursor:pointer"><title>${escapeHtml(observationLabel(model.commits[index]))} · 缺测</title>×</text>`;
  svg += '</svg>';
  $(id).innerHTML = svg;
  $(id).querySelectorAll('.missing-point').forEach(point => {
    point.onclick = () => { state.target = Number(point.dataset.rev); render(); };
  });
  $(id).querySelectorAll('.point').forEach(point => {
    const index = Number(point.dataset.rev);
    const select = () => { state.target = index; render(); };
    point.onclick = select;
    point.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } };
    point.onmouseenter = () => { $('hoverReadout').textContent = `${model.commits[index].short_commit}  ${format(values[index])}`; };
    point.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function drawMultiChart(id, series, {sharedScale = false, connect = canCompare,
  format = value => state.mode === 'relative' ? `${signed(value, 2)}%` : formatCounterValue(value)} = {}) {
  const usable = series.flatMap(item => item.values.filter(finite));
  if (!usable.length) { $(id).innerHTML = '<div class="empty-state">当前筛选没有可用数据</div>'; return; }
  const W = 1120, H = 274, pad = {l:68, r:28, t:28, b:50}, plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  const indices = model.commits.map((_, index) => index);
  const x = index => pad.l + indices.indexOf(index) * plotWidth / Math.max(1, indices.length - 1);
  // IPC 的变化通常只有几个百分点，而计数器可能变化几十甚至几百个百分点。
  // 每个序列使用自己的纵向范围，保留真实值用于 tooltip，从视觉上避免小波动被压扁。
  const scales = series.map(item => {
    const values = sharedScale ? usable : item.values.filter(finite);
    let low = Math.min(...(sharedScale ? values : [0, ...values]));
    let high = Math.max(...(sharedScale ? values : [0, ...values])), spread = high - low;
    if (spread < 1e-8) spread = Math.max(Math.abs(high) * 0.04, 0.04);
    return {low: low - spread * 0.2, high: high + spread * 0.2};
  });
  const y = (value, seriesIndex) => {
    const scale = scales[seriesIndex];
    return pad.t + (scale.high - value) / (scale.high - scale.low) * plotHeight;
  };
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(currentName())} 趋势">`;
  for (let step = 0; step <= 4; step++) {
    const gridY = pad.t + plotHeight * step / 4;
    svg += `<line x1="${pad.l}" y1="${gridY}" x2="${W - pad.r}" y2="${gridY}" stroke="#e9eef5"/>`;
    if (sharedScale) {
      const value = scales[0].high - (scales[0].high - scales[0].low) * step / 4;
      svg += `<text x="${pad.l - 13}" y="${gridY + 4}" text-anchor="end" fill="#8795a9" font-size="11">${escapeHtml(format(value))}</text>`;
    }
  }
  if (!sharedScale) svg += `<text x="${pad.l - 13}" y="${pad.t + 4}" text-anchor="end" fill="#8795a9" font-size="10">高</text><text x="${pad.l - 13}" y="${H - pad.b + 4}" text-anchor="end" fill="#8795a9" font-size="10">低</text>`;
  for (const [index, letter, lineColor] of [[state.base, 'A', '#7d8ba0'], [state.target, 'B', '#2f63db']]) if (indices.includes(index)) svg += `<line x1="${x(index)}" y1="${pad.t}" x2="${x(index)}" y2="${H - pad.b}" stroke="${lineColor}" stroke-dasharray="4 5" opacity=".55"/><text x="${x(index)}" y="16" text-anchor="middle" fill="${lineColor}" font-size="11" font-weight="700">${letter}</text>`;
  series.forEach((item, seriesIndex) => {
    const color = item.color;
    const segments = []; let segment = [];
    const observed = indices.filter(index => finite(item.values[index]));
    observed.forEach((index, position) => {
      if (segment.length && !connect(observed[position - 1], index, item)) { segments.push(segment); segment = []; }
      segment.push(`${x(index)},${y(item.values[index], seriesIndex)}`);
    });
    if (segment.length) segments.push(segment);
    segments.filter(points => points.length > 1).forEach(points => { svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="${item.kind === 'ipc' ? '2.8' : '2.1'}" stroke-linejoin="round" ${item.kind === 'counter' ? 'stroke-dasharray="5 3"' : ''}/>`; });
    item.values.forEach((value, index) => { if (finite(value)) svg += `<circle class="multi-point" data-rev="${index}" data-series="${seriesIndex}" cx="${x(index)}" cy="${y(value, seriesIndex)}" r="${index === state.target ? 4.8 : 3.5}" fill="white" stroke="${color}" stroke-width="2"><title>${escapeHtml(item.label)} · ${escapeHtml(observationLabel(model.commits[index]))} · ${format(value)}</title></circle>`; });
  });
  indices.forEach((index, position) => { const commit = model.commits[index]; if (position % Math.ceil(indices.length / 10) === 0 || index === state.base || index === state.target || position === indices.length - 1) svg += `<text x="${x(index)}" y="${H - 18}" text-anchor="middle" fill="${index === state.target ? '#2f63db' : '#7f8da1'}" font-size="11" font-weight="${index === state.target ? 700 : 500}">${commit.short_commit.slice(0, 7)}</text>`; });
  for (const index of indices.filter(index => !series.some(item => finite(item.values[index])))) svg += `<text class="missing-point" data-rev="${index}" x="${x(index)}" y="${H - pad.b}" text-anchor="middle" fill="#8795a9" style="cursor:pointer"><title>${escapeHtml(observationLabel(model.commits[index]))} · 缺测</title>×</text>`;
  svg += '</svg>'; $(id).innerHTML = svg;
  $(id).querySelectorAll('.missing-point').forEach(point => {
    point.onclick = () => { state.target = Number(point.dataset.rev); render(); };
  });
  $(id).querySelectorAll('.multi-point').forEach(point => {
    const index = Number(point.dataset.rev), item = series[Number(point.dataset.series)];
    const select = () => { state.target = index; render(); };
    point.onclick = select;
    point.onmouseenter = () => { $('hoverReadout').textContent = `${item.label} · ${model.commits[index].short_commit}  ${format(item.values[index])}`; };
    point.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function comparisonData(from = state.base, to = state.target) {
  return model.workloads.map(workload => {
    const pair = workloadAssessment(workload, from, to);
    return {workload, a:pair.a, b:pair.b, delta:pair.allowed ? (pair.b / pair.a - 1) * 100 : null,
      count:pair.count, total:pair.total};
  });
}

function counterKpiCards() {
  const metric = selectedCounterMetric();
  const a = counterValue(state.counterSlice, state.base), b = counterValue(state.counterSlice, state.target);
  const delta = counterPerformanceChange(state.counterSlice);
  const unit = counterUnitLabel(metric);
  const current = correlationRanking().find(row => row.metric.metric_id === state.counterMetric);
  return [
    {label:'计数器 A', value:formatCounterValue(a), note:`${model.commits[state.base].short_commit} · ${unit}`, tone:'neutral'},
    {label:'计数器 B', value:formatCounterValue(b), note:`${model.commits[state.target].short_commit} · ${unit}`, tone:'neutral'},
    {label:'A/B 性能变化', value:pct(delta, 3), note:metric.direction === 'lower_is_better' ? '数值越低越好' : '数值越高越好', tone:tone(delta)},
    {label:'当前相关性 ρ', value:current ? signed(current.rho, 2) : '—', note:current ? `${correlationStrength(current.rho)}相关 · ${current.n} 个样本` : '样本不足', tone:'neutral'}
  ];
}

function entityKpiCards() {
  const isSlice = state.level === 'slice';
  const slice = model.sliceIndex.get(state.slice);
  const pair = isSlice ? null : workloadAssessment(state.workload);
  const a = isSlice ? sliceIPC(slice, state.base) : pair.a;
  const b = isSlice ? sliceIPC(slice, state.target) : pair.b;
  const scope = pair?.common && pair.allowed ? ` · 共同 ${pair.count}/${pair.total} 切片` : '';
  const delta = isSlice ? ipcChangePct(state.slice, state.base, state.target) : workloadChange(state.workload);
  const records = sliceRecords();
  const hasSliceData = records.some(row => finite(row.delta));
  const worst = records.filter(row => finite(row.impact) && row.impact > 0 && row.delta < -STABLE_THRESHOLD)
    .sort((left, right) => right.impact - left.impact)[0];
  return [
    {label:'IPC · A', value:finite(a) ? a.toFixed(4) : '—', note:model.commits[state.base].short_commit + scope, tone:'neutral'},
    {label:'IPC · B', value:finite(b) ? b.toFixed(4) : '—', note:currentName() + scope, tone:'neutral'},
    {label:'IPC 变化', value:pct(delta, 3), note:`${model.commits[state.base].short_commit} → ${model.commits[state.target].short_commit}`, tone:tone(delta)},
    {
      label:'主要退化切片',
      value:worst ? pct(worst.delta) : hasSliceData ? '0 项' : '—',
      note:worst ? `${state.workload} / ${worst.slice.checkpoint}` : hasSliceData ? '无超过 0.5% 的退化' : '无可比切片数据',
      tone:worst ? 'bad' : 'neutral',
      slice:worst?.slice.slice,
      workload:hasSliceData ? state.workload : null
    }
  ];
}

function suiteKpiCards() {
  const scoreDelta = scoreChange(), scorePair = scoreAssessment();
  const comparisons = comparisonData().filter(item => finite(item.delta));
  const improved = comparisons.filter(item => item.delta > STABLE_THRESHOLD).length;
  const regressed = comparisons.filter(item => item.delta < -STABLE_THRESHOLD).length;
  const stable = comparisons.length - improved - regressed;
  const worstWorkload = comparisons.filter(item => item.delta < -STABLE_THRESHOLD)
    .sort((left, right) => left.delta - right.delta)[0];
  let worstSlice = null;
  let hasSliceData = false;
  for (const slice of model.slices) {
    const delta = ipcChangePct(slice.slice, state.base, state.target);
    if (!finite(delta)) continue;
    hasSliceData = true;
    if (delta < -STABLE_THRESHOLD && (!worstSlice || delta < worstSlice.delta)) {
      worstSlice = {...slice, delta};
    }
  }
  return [
    {label:`${state.scoreSuite}/GHz 变化`, value:pct(scoreDelta, 3), note:`${formatCounterValue(suiteScore(state.base))} → ${formatCounterValue(suiteScore(state.target))} · ${scorePair.allowed ? '当前评分范围可比' : scorePair.reasons.join('；')}`, tone:tone(scoreDelta)},
    {
      label:'退化 Workload',
      value:comparisons.length ? `${regressed}<small> / ${comparisons.length}</small>` : '—',
      note:comparisons.length ? `提升 ${improved} · 稳定 ${stable}` : '无可比 Workload 数据',
      tone:regressed ? 'bad' : 'neutral',
      filter:'regressed'
    },
    {
      label:'最大 Workload 退化',
      value:worstWorkload ? pct(worstWorkload.delta) : comparisons.length ? '0 项' : '—',
      note:worstWorkload?.workload || (comparisons.length ? '无超过 0.5% 的退化' : '无可比 Workload 数据'),
      tone:worstWorkload ? 'bad' : 'neutral',
      workload:worstWorkload?.workload
    },
    {
      label:'最大切片退化',
      value:worstSlice ? pct(worstSlice.delta) : hasSliceData ? '0 项' : '—',
      note:worstSlice ? `${worstSlice.workload} / ${worstSlice.checkpoint}` : hasSliceData ? '无超过 0.5% 的退化' : '无可比切片数据',
      tone:worstSlice ? 'bad' : 'neutral',
      slice:worstSlice?.slice,
      workload:worstSlice?.workload
    }
  ];
}

function renderKpis() {
  const cards = state.level === 'counter' ? counterKpiCards()
    : state.level === 'suite' ? suiteKpiCards() : entityKpiCards();
  renderKpiCards(cards);
}

function renderKpiCards(cards) {
  $('kpis').innerHTML = cards.map(card => {
    const interactive = card.workload || card.filter;
    const tag = interactive ? 'button' : 'article';
    const action = interactive ? ` type="button" data-workload="${escapeHtml(card.workload || '')}" data-slice="${escapeHtml(card.slice || '')}" data-filter="${escapeHtml(card.filter || '')}"` : '';
    return `<${tag} class="kpi"${action}><div class="kpi-label">${card.label}</div><div class="kpi-value ${card.tone}">${card.value}</div><div class="kpi-note" title="${escapeHtml(card.note)}">${escapeHtml(card.note)}</div></${tag}>`;
  }).join('');
  $('kpis').querySelectorAll('button').forEach(button => button.onclick = () => {
    if (button.dataset.filter) {
      state.workloadFilter = button.dataset.filter;
      state.workloadSearch = '';
      $('workloadSearch').value = '';
      renderWorkloads();
      $('workloads').scrollIntoView({behavior:'smooth', block:'start'});
      return;
    }
    state.workload = button.dataset.workload;
    state.slice = button.dataset.slice || model.slicesByWorkload.get(state.workload)[0].slice;
    state.level = button.dataset.slice ? 'slice' : 'workload';
    state.metric = 'ipc';
    render();
    $('trendPanel').scrollIntoView({behavior:'smooth', block:'start'});
  });
}

function heatStyle(value) {
  if (!finite(value)) return 'color:#7d8999;background:#f1f3f6';
  const alpha = Math.min(0.45, 0.08 + Math.abs(value) / 16);
  return value > 0 ? `color:#096e61;background:rgba(22,145,123,${alpha})` : value < 0 ? `color:#a43e36;background:rgba(213,87,73,${alpha})` : 'color:#66758a;background:#f0f3f7';
}

function visibleWorkloads(rows = comparisonData()) {
  const query = state.workloadSearch.trim().toLowerCase();
  return rows.filter(row => (!query || row.workload.toLowerCase().includes(query)) &&
    (state.workloadFilter === 'all' || (finite(row.delta) && (state.workloadFilter === 'regressed' ? row.delta < -STABLE_THRESHOLD : row.delta > STABLE_THRESHOLD))))
    .sort((a, b) => {
      if (finite(a.delta) !== finite(b.delta)) return finite(a.delta) ? -1 : 1;
      return (state.workloadFilter === 'improved' ? b.delta - a.delta : a.delta - b.delta) || a.workload.localeCompare(b.workload);
    }).map(row => row.workload);
}

function renderWorkloads() {
  const rows = comparisonData();
  const counts = {all:rows.length, regressed:rows.filter(row => row.delta < -STABLE_THRESHOLD).length,
    improved:rows.filter(row => row.delta > STABLE_THRESHOLD).length};
  const labels = {all:'全部', regressed:'退化', improved:'提升'};
  document.querySelectorAll('#workloadFilters [data-filter]').forEach(button => {
    button.classList.toggle('selected', button.dataset.filter === state.workloadFilter);
    button.textContent = `${labels[button.dataset.filter]} ${counts[button.dataset.filter]}`;
  });
  const workloads = visibleWorkloads(rows);
  renderHeatmap(workloads);
  renderMovers(rows, workloads);
}

function renderHeatmap(workloads = visibleWorkloads()) {
  $('workloadCount').textContent = `${workloads.length} / ${model.workloads.length} 项 · 相对 A`;
  const header = model.commits.map((commit, index) => `<th title="${escapeHtml(commit.subject)}">${commit.short_commit.slice(0, 7)}${index === state.base ? '<b>A</b>' : index === state.target ? '<b>B</b>' : ''}</th>`).join('');
  const body = workloads.map(workload => {
    const baseRow = workloadRow(workload, state.base);
    const cells = model.commits.map((_, index) => {
      const row = workloadRow(workload, index), pair = workloadAssessment(workload, state.base, index);
      const value = pair.allowed ? (pair.b / pair.a - 1) * 100 : null;
      const partial = row && (row.status !== 'complete' || baseRow?.status !== 'complete');
      const selected = workload === state.workload && index === state.target ? ' current' : '';
      return `<td><button type="button" class="heat-cell${selected}" data-workload="${escapeHtml(workload)}" data-rev="${index}" style="${heatStyle(value)}" title="${escapeHtml(workload)} · ${model.commits[index].short_commit}: ${pct(value)}${pair.common && pair.allowed ? ` · 共同 ${pair.count}/${pair.total} 切片` : partial ? ' · 部分覆盖' : ''}">${finite(value) ? signed(value, 1) : '—'}${partial ? '*' : ''}</button></td>`;
    }).join('');
    return `<tr><th><button type="button" class="workload-name" data-workload="${escapeHtml(workload)}" data-rev="${state.target}" title="查看 ${escapeHtml(workload)}">${escapeHtml(workload)}</button></th>${cells}</tr>`;
  }).join('');
  const empty = canCompare() ? '没有匹配的 Workload' : '当前 A/B 不可比，请调整基线或目标';
  $('heatTable').innerHTML = `<thead><tr><th>Workload</th>${header}</tr></thead><tbody>${body || `<tr><td colspan="${model.commits.length + 1}" class="anomaly-empty">${empty}</td></tr>`}</tbody>`;
  $('heatTable').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.workload = button.dataset.workload;
    state.target = Number(button.dataset.rev);
    state.level = 'workload';
    state.metric = 'ipc';
    state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
  });
}

function renderMovers(rows = comparisonData(), workloads = visibleWorkloads(rows)) {
  const all = rows.filter(item => finite(item.delta) && item.a > 0 && item.b > 0);
  const count = all.length || 1;
  const contributions = all.map(item => ({...item, contribution: Math.log(item.b / item.a) / count * 100}));
  const visible = new Set(workloads);
  const shown = contributions.filter(item => visible.has(item.workload)).sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).slice(0, 12);
  const max = Math.max(0.01, ...shown.map(item => Math.abs(item.contribution)));
  $('bars').innerHTML = shown.map(item => `<button type="button" class="bar-row" data-workload="${escapeHtml(item.workload)}" title="查看 ${escapeHtml(item.workload)} · 共同 ${item.count}/${item.total} 切片 · IPC ${item.a.toFixed(6)} → ${item.b.toFixed(6)}"><span>${escapeHtml(item.workload)}</span><span class="bar-track"><i class="bar-fill ${item.contribution >= 0 ? 'positive' : 'negative'}" style="${item.contribution >= 0 ? 'left:50%' : 'right:50%'};width:${Math.abs(item.contribution) / max * 48}%"></i></span><strong class="${tone(item.delta)}">${pct(item.delta, 2)}</strong></button>`).join('') || '<div class="anomaly-empty compact">暂无可比变化</div>';
  $('bars').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.workload = button.dataset.workload;
    state.level = 'workload';
    state.metric = 'ipc';
    state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
    document.querySelector('.trend-panel').scrollIntoView({behavior:'smooth', block:'start'});
  });
  const net = all.length ? Math.expm1(contributions.reduce((sum, item) => sum + item.contribution, 0) / 100) * 100 : null;
  $('net').textContent = pct(net, 3);
  $('net').className = tone(net);
  $('netScope').textContent = `共同 ${all.reduce((sum, item) => sum + item.count, 0)}/${rows.reduce((sum, item) => sum + item.total, 0)} 切片 · ${all.length}/${model.workloads.length} Workload`;
  $('net').title = `${$('netScope').textContent} · 按共同切片重算 IPC 后取变化比的几何平均`;
}

function sliceRecords() {
  return pairSlices(model.slicesByWorkload.get(state.workload) || []).map(slice => {
    const a = sliceIPC(slice, state.base), b = sliceIPC(slice, state.target);
    const delta = ipcChangePct(slice.slice, state.base, state.target);
    const impact = sliceImpact(slice.slice);
    const comparison = sliceAssessment(slice.slice);
    if (comparison.allowed && !finite(impact)) comparison.notes.push('两侧权重不同，未计算加权贡献');
    return {slice, a, b, delta, impact, comparison};
  });
}

function renderSlices() {
  if (state.level === 'counter') {
    renderCounterSlices();
    return;
  }
  const records = sliceRecords();
  records.sort((a, b) => {
    if (state.sort === 'weight') return b.slice.weight - a.slice.weight;
    if (state.sort === 'change') return (finite(b.delta) ? Math.abs(b.delta) : -1) - (finite(a.delta) ? Math.abs(a.delta) : -1);
    return (finite(b.impact) ? Math.abs(b.impact) : -1) - (finite(a.impact) ? Math.abs(a.impact) : -1);
  });
  $('sliceTitle').textContent = `${state.workload} · 切片 A/B 对比`;
  $('sliceRows').innerHTML = records.map(record => {
    const status = comparisonLabel(record.comparison, record.delta);
    return `<tr data-detail-slice="${escapeHtml(record.slice.slice)}" class="${record.slice.slice === state.slice ? 'selected-row' : ''}"><td><button type="button" class="slice-btn" data-slice="${escapeHtml(record.slice.slice)}" title="${escapeHtml(sliceName(record.slice.slice))} · ${escapeHtml(record.slice.checkpoint_identity || record.slice.slice)}"><strong>${escapeHtml(record.slice.checkpoint)}</strong></button></td><td>${(record.slice.weight * 100).toFixed(3)}%</td><td>${finite(record.a) ? record.a.toFixed(4) : '—'}</td><td>${finite(record.b) ? record.b.toFixed(4) : '—'}</td><td class="${tone(record.delta)}">${pct(record.delta)}</td><td class="${finite(record.impact) ? record.impact > 0 ? 'bad' : record.impact < 0 ? 'good' : 'neutral' : 'neutral'}">${signed(record.impact, 6)}</td><td>${status}</td></tr>`;
  }).join('');
  bindSliceCounterRows();
  const comparable = records.filter(record => finite(record.delta));
  const improved = comparable.filter(record => record.delta > STABLE_THRESHOLD).length;
  const regressed = comparable.filter(record => record.delta < -STABLE_THRESHOLD).length;
  const netImpact = comparable.length && comparable.every(row => finite(row.impact)) ? comparable.reduce((sum, record) => sum + record.impact, 0) : null;
  const weightCoverage = comparable.reduce((sum, record) => sum + record.slice.weight, 0) * 100;
  $('sliceSummary').innerHTML = `<span>可比切片 <b>${comparable.length} / ${records.length}</b></span><span>提升 <b class="good">${improved}</b></span><span>退化 <b class="bad">${regressed}</b></span><span>净加权 ΔCPI <b class="${netImpact > 0 ? 'bad' : netImpact < 0 ? 'good' : 'neutral'}">${signed(netImpact, 6)}</b></span><span>权重覆盖 <b>${weightCoverage.toFixed(3)}%</b></span>`;
}

function renderCounterSlices() {
  const metric = selectedCounterMetric();
  const workload = currentCounterWorkload();
  const records = counterAnalysisSlices().map(slice => {
    const a = counterValue(slice.slice, state.base), b = counterValue(slice.slice, state.target);
    return {slice, a, b, delta:counterPerformanceChange(slice.slice), comparison:counterAssessment(slice.slice)};
  }).sort((a, b) => (finite(b.delta) ? Math.abs(b.delta) : -1) - (finite(a.delta) ? Math.abs(a.delta) : -1));
  $('sliceTitle').textContent = `${workload} · ${metric.display_name} · 切片 A/B 对比`;
  $('valueAHead').textContent = `${counterUnitLabel(metric)} · A`;
  $('valueBHead').textContent = `${counterUnitLabel(metric)} · B`;
  $('changeHead').textContent = '性能变化';
  $('impactHead').textContent = '数值变化';
  $('impactHead').title = '计数器数值 B − A';
  $('sort').disabled = true;
  $('sliceRows').innerHTML = records.map(record => {
    const raw = record.comparison.allowed && finite(record.a) && finite(record.b) ? record.b - record.a : null;
    if (record.comparison.allowed && !finite(record.delta)) record.comparison.notes.push('比值分母为0，保留数值差值');
    const status = comparisonLabel(record.comparison, record.delta);
    return `<tr data-detail-slice="${escapeHtml(record.slice.slice)}" class="${record.slice.slice === state.counterSlice ? 'selected-row' : ''}"><td><button type="button" class="slice-btn" data-counter-slice="${escapeHtml(record.slice.slice)}" title="${escapeHtml(sliceName(record.slice.slice))} · ${escapeHtml(record.slice.checkpoint_identity || record.slice.slice)}"><strong>${escapeHtml(record.slice.checkpoint)}</strong></button></td><td>${(record.slice.weight * 100).toFixed(3)}%</td><td>${formatCounterValue(record.a)}</td><td>${formatCounterValue(record.b)}</td><td class="${tone(record.delta)}">${pct(record.delta)}</td><td>${finite(raw) ? signed(raw, 0) : '—'}</td><td>${status}</td></tr>`;
  }).join('');
  bindSliceCounterRows();
  const comparable = records.filter(record => record.comparison.allowed);
  const improved = comparable.filter(record => record.delta > STABLE_THRESHOLD).length;
  const regressed = comparable.filter(record => record.delta < -STABLE_THRESHOLD).length;
  $('sliceSummary').innerHTML = `<span>可比切片 <b>${comparable.length} / ${records.length}</b></span><span>改善 <b class="good">${improved}</b></span><span>退化 <b class="bad">${regressed}</b></span><span>方向 <b>${metric.direction === 'lower_is_better' ? '越低越好' : '越高越好'}</b></span><span>口径 <b>${counterUnitLabel(metric)}</b></span><span>窗口 <b>final PERF dump</b></span>`;
}

function bindSliceCounterRows() {
  $('sliceRows').querySelectorAll('[data-detail-slice]').forEach(row => row.onclick = () => {
    state.slice = state.counterSlice = row.dataset.detailSlice;
    state.workload = model.sliceIndex.get(state.slice).workload;
    if (state.level !== 'counter') {
      state.level = 'slice';
      state.metric = 'ipc';
    }
    render();
    $('sliceCounters').scrollIntoView({behavior:'smooth', block:'start'});
  });
}

function sliceCounterRecords(slice) {
  return model.counterMetrics.map(metric => {
    const a = counterValue(slice, state.base, metric.metric_id);
    const b = counterValue(slice, state.target, metric.metric_id);
    const comparison = counterAssessment(slice, state.base, state.target, metric.metric_id);
    const delta = comparison.allowed ? b - a : null;
    const change = comparison.allowed ? a === 0 ? b === 0 ? 0 : null : (b / a - 1) * 100 : null;
    return {metric, a, b, delta, change, comparison};
  }).sort((a, b) => {
    const magnitude = row => finite(row.change) ? Math.abs(row.change) : finite(row.delta) && row.delta !== 0 ? Infinity : -1;
    return magnitude(b) - magnitude(a);
  });
}

function renderSliceCounters() {
  const slice = state.level === 'counter' ? state.counterSlice : state.slice;
  const panel = $('sliceCounters');
  panel.hidden = !['slice', 'counter'].includes(state.level) || !model.sliceIndex.has(slice);
  if (panel.hidden) return;
  const point = model.sliceIndex.get(slice);
  const base = model.commits[state.base], target = model.commits[state.target];
  const records = sliceCounterRecords(slice);
  const comparable = records.filter(row => row.comparison.allowed);
  const ipcA = ipcAt(slice, state.base), ipcB = ipcAt(slice, state.target);
  const ipcChange = ipcChangePct(slice, state.base, state.target);
  const formatIPC = value => finite(value) ? value.toFixed(4) : '—';
  const formatValue = value => finite(value) ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits:state.counterUnit === 'per_kinst' ? 6 : 2}).format(value) : '—';
  $('sliceCounterTitle').textContent = `${point.workload} / ${point.checkpoint} · 性能计数器变化`;
  $('sliceCounterContext').textContent = `A ${base.short_commit} → B ${target.short_commit} · 切片 IPC ${formatIPC(ipcA)} → ${formatIPC(ipcB)} · IPC 变化 ${pct(ipcChange)}`;
  $('sliceCounterUnit').value = state.counterUnit;
  $('sliceCounterAHead').textContent = `A · ${base.short_commit}`;
  $('sliceCounterBHead').textContent = `B · ${target.short_commit}`;
  $('sliceCounterSummary').innerHTML = `<span>可比 <b>${comparable.length} / ${records.length}</b> 项 · 按变化幅度排序</span><span><i class="counter-increase"></i>数值增加 <i class="counter-decrease"></i>数值减少</span>`;
  const maximum = Math.max(1, ...records.filter(row => finite(row.change)).map(row => Math.abs(row.change)));
  $('sliceCounterRows').innerHTML = records.map(row => {
    const {metric, a, b, delta, change, comparison} = row;
    const direction = finite(delta) && delta !== 0 ? delta > 0 ? 'counter-increase' : 'counter-decrease' : 'neutral';
    const label = !comparison.allowed ? comparison.reasons.join('；') : a === 0 && b !== 0 ? '基线为 0，百分比未定义' : '可比';
    const bar = finite(change) && change !== 0 ? `<i class="${direction}" style="width:${Math.abs(change) / maximum * 48}%"></i>` : '';
    return `<tr data-detail-metric="${escapeHtml(metric.metric_id)}"><td><strong>${escapeHtml(metric.display_name)}</strong><small>${escapeHtml(metric.category)} · ${escapeHtml(counterUnitLabel(metric))}</small></td><td>${formatValue(a)}</td><td>${formatValue(b)}</td><td class="${direction}">${finite(delta) ? `${delta > 0 ? '+' : ''}${formatValue(delta)}` : '—'}</td><td><div class="slice-counter-change"><span class="${direction}">${pct(change)}</span><div class="slice-counter-bar">${bar}</div></div></td><td class="slice-counter-status" title="${escapeHtml([...comparison.reasons,...comparison.notes].join('；'))}">${escapeHtml(label)}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="neutral">当前数据集没有常用计数器数据</td></tr>';
}

function median(values) {
  const usable = values.filter(finite).sort((a, b) => a - b);
  if (!usable.length) return null;
  const middle = Math.floor(usable.length / 2);
  return usable.length % 2 ? usable[middle] : (usable[middle - 1] + usable[middle]) / 2;
}

function leastSquares(points) {
  if (points.length < 3) return null;
  const meanX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  let covariance = 0, variance = 0;
  for (const point of points) {
    covariance += (point.x - meanX) * (point.y - meanY);
    variance += (point.x - meanX) ** 2;
  }
  if (variance <= 0) return null;
  const slope = covariance / variance;
  return {slope, intercept: meanY - slope * meanX};
}

function scatterAxisFormat(isPercent) {
  if (isPercent) return value => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
  return value => Math.abs(value) >= 1000 ? `${(value / 1000).toFixed(1)}k` : Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(2);
}

// 参考 thp_seed_cmp 的关键路径散点：x 轴是计数器，y 轴是 IPC，
// 用 Spearman ρ 判断这个计数器是否真的和性能同向变化。
function drawScatter(id, pairs, {xLabel, yLabel, xPct, yPct, rho, mode}) {
  const points = pairs.filter(pair => finite(pair.x) && finite(pair.y));
  if (!points.length) { $(id).innerHTML = '<div class="empty-state">当前视角没有可用数据</div>'; return; }
  const W = 880, H = 360, pad = {l:74, r:30, t:34, b:62};
  const plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  const bounds = (values, isPercent) => {
    const floor = Math.min(...values);
    let low = floor, high = Math.max(...values), span = high - low;
    if (span < 1e-9) span = Math.max(Math.abs(high) * 0.08, isPercent ? 0.5 : 1e-6);
    low -= span * 0.14; high += span * 0.14;
    if (isPercent) { low = Math.min(low, 0); high = Math.max(high, 0); }
    else if (floor >= 0) low = Math.max(low, 0);
    return [low, high];
  };
  const [xLow, xHigh] = bounds(points.map(point => point.x), xPct);
  const [yLow, yHigh] = bounds(points.map(point => point.y), yPct);
  const X = value => pad.l + (value - xLow) / (xHigh - xLow) * plotWidth;
  const Y = value => pad.t + (yHigh - value) / (yHigh - yLow) * plotHeight;
  const xFormat = scatterAxisFormat(xPct), yFormat = scatterAxisFormat(yPct);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(xLabel)} 与 ${escapeHtml(yLabel)} 的关系">`;
  for (let step = 0; step <= 4; step++) {
    const value = yLow + (yHigh - yLow) * step / 4;
    svg += `<line x1="${pad.l}" y1="${Y(value)}" x2="${W - pad.r}" y2="${Y(value)}" stroke="#e9eef5"/>`;
    svg += `<text x="${pad.l - 12}" y="${Y(value) + 4}" text-anchor="end" fill="#8795a9" font-size="11">${escapeHtml(yFormat(value))}</text>`;
  }
  for (let step = 0; step <= 4; step++) {
    const value = xLow + (xHigh - xLow) * step / 4;
    svg += `<text x="${X(value)}" y="${H - pad.b + 20}" text-anchor="middle" fill="#8795a9" font-size="11">${escapeHtml(xFormat(value))}</text>`;
  }
  if (xPct && xLow < 0 && xHigh > 0) svg += `<line x1="${X(0)}" y1="${pad.t}" x2="${X(0)}" y2="${H - pad.b}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;
  if (yPct && yLow < 0 && yHigh > 0) svg += `<line x1="${pad.l}" y1="${Y(0)}" x2="${W - pad.r}" y2="${Y(0)}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;

  const fit = leastSquares(points);
  if (fit) {
    const y1 = fit.slope * xLow + fit.intercept, y2 = fit.slope * xHigh + fit.intercept;
    svg += `<line x1="${X(xLow)}" y1="${Y(y1)}" x2="${X(xHigh)}" y2="${Y(y2)}" stroke="#8fa4c4" stroke-width="1.8" stroke-dasharray="6 5" opacity=".85"/>`;
  }

  const weights = points.map(point => point.weight);
  const weightMax = Math.max(...weights), weightMin = Math.min(...weights);
  const weightSpan = weightMax - weightMin || 1;
  const radius = point => 5 + 9 * Math.sqrt((point.weight - weightMin) / weightSpan);
  const yMedian = median(points.map(point => point.y));
  const better = point => yPct ? point.y >= 0 : point.y >= yMedian;

  const labelled = new Map();
  const remember = point => { if (point && !labelled.has(point.slice)) labelled.set(point.slice, point); };
  for (const point of [...points].sort((a, b) => b.weight - a.weight).slice(0, 3)) remember(point);
  remember(points.reduce((best, point) => !best || point.x > best.x ? point : best, null));
  remember(points.reduce((best, point) => !best || point.x < best.x ? point : best, null));
  remember(points.reduce((best, point) => !best || point.y > best.y ? point : best, null));
  remember(points.reduce((best, point) => !best || point.y < best.y ? point : best, null));

  for (const point of points) {
    const cx = X(point.x), cy = Y(point.y), selected = point.slice === state.counterSlice;
    svg += `<circle class="point" data-slice="${escapeHtml(point.slice)}" cx="${cx}" cy="${cy}" r="${radius(point).toFixed(2)}" fill="${better(point) ? '#16917b' : '#d55749'}" fill-opacity="${selected ? '0.95' : '0.6'}" stroke="${selected ? '#14233a' : (better(point) ? '#0d6f5e' : '#a8402f')}" stroke-width="${selected ? '2.6' : '1.3'}" tabindex="0" role="button"><title>${escapeHtml(sliceName(point.slice))} · ${escapeHtml(xLabel)} ${xFormat(point.x)} · IPC ${yFormat(point.y)} · 权重 ${(point.weight * 100).toFixed(2)}%</title></circle>`;
  }
  for (const point of labelled.values()) {
    const cx = X(point.x), cy = Y(point.y);
    const anchor = cx > W - pad.r - 96 ? 'end' : 'start';
    svg += `<text x="${cx + (anchor === 'end' ? -8 : 8)}" y="${cy - radius(point) - 4}" text-anchor="${anchor}" fill="#5d6e86" font-size="10" font-weight="600">${escapeHtml(sliceLabel(point.slice))}</text>`;
  }
  svg += `<text x="${W - pad.r}" y="16" text-anchor="end" fill="${finite(rho) && Math.abs(rho) >= 0.4 ? '#2f63db' : '#8795a9'}" font-size="12" font-weight="700">Spearman ρ = ${finite(rho) ? signed(rho, 3) : '—'}</text>`;
  svg += `<text x="${pad.l}" y="16" fill="#8795a9" font-size="10">${escapeHtml(mode === 'level' ? '灰色虚线为最小二乘拟合' : '过零虚线表示性能不变')}</text>`;
  svg += `<text x="${pad.l + plotWidth / 2}" y="${H - 10}" text-anchor="middle" fill="#6d7c91" font-size="11">${escapeHtml(xLabel)}</text>`;
  svg += `<text transform="translate(16,${pad.t + plotHeight / 2}) rotate(-90)" text-anchor="middle" fill="#6d7c91" font-size="11">${escapeHtml(yLabel)}</text>`;
  svg += '</svg>';
  $(id).innerHTML = svg;
  $(id).querySelectorAll('.point').forEach(node => {
    const slice = node.dataset.slice;
    const select = () => { state.counterSlice = slice; render(); };
    node.onclick = select;
    node.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } };
    node.onmouseenter = () => { $('hoverReadout').textContent = slice; };
    node.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function renderRelation() {
  const metric = selectedCounterMetric();
  const mode = state.relationMode;
  const pairs = relationPairs(mode);
  const {rho, n} = spearman(pairs);
  const labels = relationAxisLabels(mode, metric);
  drawScatter('relationScatter', pairs, {xLabel: labels.x, yLabel: labels.y, xPct: labels.xPct, yPct: labels.yPct, rho, mode});

  const chipClass = !finite(rho) || Math.abs(rho) < 0.2 ? 'muted' : rho > 0 ? 'pos' : 'neg';
  const direction = !finite(rho) ? '样本不足，无法判断方向'
    : Math.abs(rho) < 0.2 ? '方向不明确，这个计数器不单独预测 IPC'
    : rho > 0 ? '正相关：计数器越大，IPC 越高'
    : '负相关：计数器越大，IPC 越低';
  const expected = metric.direction === 'lower_is_better'
    ? '该指标越低越好，预期与 IPC 负相关'
    : '该指标越高越好，预期与 IPC 正相关';
  $('relationSub').textContent = relationCaption(mode);
  $('relationStats').innerHTML = `<span class="rho-chip ${chipClass}" title="${escapeHtml(`${direction}；${expected}`)}"><i></i>Spearman ρ ${finite(rho) ? signed(rho, 3) : '—'}</span>`
    + `<span>${correlationStrength(rho)}相关</span>`
    + `<span>样本 <b>${n}</b></span>`
    + '<span title="IPC summary 与 PERF dump 窗口不同，相关性仅用于定位线索。">相关性 ≠ 因果</span>';
}

function correlationRanking(mode = state.relationMode) {
  return model.counterMetrics.map(metric => {
    const {rho, n} = spearman(relationPairs(mode, metric.metric_id));
    return {metric, rho, n};
  }).filter(row => finite(row.rho) && row.metric.metric_id !== BASE_METRIC).sort((a, b) => Math.abs(b.rho) - Math.abs(a.rho));
}

function renderCorrelationBars() {
  const rows = correlationRanking();
  $('rankingCount').textContent = `${rows.length} / ${model.counterMetrics.length} 项`;
  $('correlationBars').innerHTML = rows.map((row, index) => {
    const selected = row.metric.metric_id === state.counterMetric;
    const positive = row.rho >= 0;
    const width = Math.min(50, Math.abs(row.rho) * 50);
    return `<button type="button" class="corr-row${selected ? ' selected' : ''}" data-metric="${escapeHtml(row.metric.metric_id)}" title="${escapeHtml(row.metric.display_name)} · ρ=${signed(row.rho, 3)} · n=${row.n}">`
      + `<span>${index + 1}. ${escapeHtml(row.metric.display_name)}<em>${escapeHtml(row.metric.category)}</em></span>`
      + `<span class="corr-track"><i class="corr-fill ${positive ? 'pos' : 'neg'}" style="${positive ? 'left:50%' : 'right:50%'};width:${width}%"></i></span>`
      + `<span class="corr-value ${positive ? 'pos' : 'neg'}">${signed(row.rho, 2)}</span>`
      + `<span class="corr-flag">${selected ? '当前 ✓' : ''}</span></button>`;
  }).join('');
  $('correlationBars').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.counterMetric = button.dataset.metric;
    if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    render();
  });
}

function renderCounterMatrix() {
  const metric = selectedCounterMetric();
  const slices = [...counterAnalysisSlices()].sort((a, b) => {
    const deltaA = ipcChangePct(a.slice, state.base, state.target), deltaB = ipcChangePct(b.slice, state.base, state.target);
    return (finite(deltaB) ? deltaB : -Infinity) - (finite(deltaA) ? deltaA : -Infinity);
  });
  const head = `<tr><th class="corner">计数器</th>`
    + slices.map(slice => `<th class="matrix-slice-head" title="${escapeHtml(sliceName(slice.slice))}"><b>${escapeHtml(String(slice.checkpoint))}</b>${pct(ipcChangePct(slice.slice, state.base, state.target), 2)}</th>`).join('')
    + `<th>中位 Δ%</th></tr>`;
  const categories = new Map();
  for (const item of model.counterMetrics) {
    if (!categories.has(item.category)) categories.set(item.category, []);
    categories.get(item.category).push(item);
  }
  let body = '';
  for (const [category, metrics] of categories) {
    body += `<tr class="cat-row"><th colspan="${slices.length + 2}">${escapeHtml(category)}</th></tr>`;
    for (const item of metrics) {
      const deltas = slices.map(slice => counterChangePct(slice.slice, state.base, state.target, item.metric_id));
      const cells = slices.map((slice, index) => {
        const delta = deltas[index];
        const assessment = counterAssessment(slice.slice, state.base, state.target, item.metric_id);
        const explanation = assessment.allowed ? (finite(delta) ? assessment.notes.join('；') : 'A为0，百分比未定义；可查看原始差值') : assessment.reasons.join('；');
        const directional = finite(delta) && item.direction === 'lower_is_better' ? -delta : delta;
        const current = slice.slice === state.counterSlice ? ' current' : '';
        return `<td><div class="matrix-cell${current}" style="${heatStyle(directional)}" title="${escapeHtml(sliceName(slice.slice))} · ${escapeHtml(item.display_name)}: ${pct(delta, 2)} · ${escapeHtml(explanation)}">${finite(delta) ? signed(delta, 0) : '—'}</div></td>`;
      }).join('');
      const middle = median(deltas);
      const middleDirectional = finite(middle) && item.direction === 'lower_is_better' ? -middle : middle;
      body += `<tr class="metric-row${item.metric_id === metric.metric_id ? ' selected' : ''}" data-metric="${escapeHtml(item.metric_id)}"><th class="metric-head"><button type="button" title="${escapeHtml(item.display_name)}">${escapeHtml(item.display_name)}</button></th>${cells}<td><span class="matrix-median" style="${heatStyle(middleDirectional)}">${finite(middle) ? signed(middle, 1) : '—'}</span></td></tr>`;
    }
  }
  $('counterMatrix').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  $('counterMatrix').querySelectorAll('tr.metric-row').forEach(row => row.onclick = () => {
    state.counterMetric = row.dataset.metric;
    if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    render();
  });
  $('matrixSub').textContent = `${currentCounterWorkload()} · ${model.commits[state.base].short_commit} → ${model.commits[state.target].short_commit} · ${state.counterUnit === 'per_kinst' ? '每千指令' : '原始计数'}口径 · 绿色代表该计数器朝有利方向变化`;
}

function renderTransitions() {
  const transitions = [];
  for (let b = 1; b < model.commits.length; b++) {
    const a = b - 1;
    if (!Comparison.runs(model.commits[a], model.commits[b]).allowed) continue;
    const values = pairSlices(model.slices, a, b).map(slice => ipcChangePct(slice.slice, a, b)).filter(finite).sort((x, y) => x - y);
    if (!values.length) continue;
    transitions.push({a, b, count:values.length, median:median(values),
      improved:values.filter(value => value > STABLE_THRESHOLD).length,
      regressed:values.filter(value => value < -STABLE_THRESHOLD).length});
  }
  $('transitionCount').textContent = `${transitions.length} 个可比区间`;
  $('transitions').innerHTML = transitions.map(row => {
    const stable = row.count - row.improved - row.regressed;
    return `<article class="transition-card"><div class="transition-head"><span>${model.commits[row.a].short_commit} → ${model.commits[row.b].short_commit}</span><strong class="${tone(row.median)}">${pct(row.median, 3)}</strong></div><div class="stack"><i class="up" style="width:${row.improved / row.count * 100}%"></i><i class="steady" style="width:${stable / row.count * 100}%"></i><i class="down" style="width:${row.regressed / row.count * 100}%"></i></div><div class="transition-meta">提升 ${row.improved} · 稳定 ${stable} · 退化 ${row.regressed} · ${row.count} 诊断可比切片</div></article>`;
  }).join('') || '<div class="anomaly-empty">当前切片集的相邻测量没有已确认的可比条件。</div>';
}

function renderAnomalies() {
  const report = model.anomalies;
  if (!report?.comparisons?.length) {
    $('anomalyPolicy').textContent = '暂无可比基线';
    $('anomalyComparisons').innerHTML = '<div class="anomaly-empty">当前数据没有可用于异常检测的历史基线。</div>';
    return;
  }
  const policy = report.policy;
  $('anomalyPolicy').textContent = `Workload / Slice 阈值 ${policy.workload_cpi_degradation_pct}% / ${policy.slice_cpi_degradation_pct}%`;
  $('anomalyComparisons').innerHTML = report.comparisons.map(comparison => {
    const workloads = comparison.workloads.filter(row => row.is_anomaly);
    const slices = comparison.workloads.flatMap(row => row.anomalous_slices.map(slice => ({workload:row.workload, ...slice})))
      .sort((a, b) => b.weighted_cpi_contribution - a.weighted_cpi_contribution).slice(0, 8);
    const baseIndex = model.commits.findIndex(commit => commit.run_id === comparison.baseline.run_id);
    const targetIndex = model.commits.findIndex(commit => commit.run_id === comparison.current.run_id);
    const label = comparison.label === 'fixed' ? '固定验收基线' : comparison.baseline_basis === 'tested_parent' ? '已测试父提交' : '最近可比观测';
    const workloadRows = workloads.length ? workloads.map(row => `<button type="button" class="anomaly-workload" data-kind="workload" data-workload="${escapeHtml(row.workload)}" data-base="${baseIndex}" data-target="${targetIndex}"><span>${escapeHtml(row.workload)}</span><strong class="bad">CPI ${pct(row.cpi_degradation_percent, 3)}</strong><small>ΔCPI ${signed(row.weighted_cpi_delta, 6)} · ${row.anomalous_slice_count} slices</small></button>`).join('') : '<div class="anomaly-empty compact">没有超过阈值的 workload</div>';
    const sliceRows = slices.length ? slices.map(row => {
      const clues = row.counter_clues.slice(0, 3).map(clue => clue.display_name).join(' · ') || '暂无显著计数器变化';
      return `<tr><td><button type="button" class="anomaly-slice" data-kind="slice" data-workload="${escapeHtml(row.workload)}" data-slice="${escapeHtml(row.slice_id)}" data-base="${baseIndex}" data-target="${targetIndex}"><b>${escapeHtml(row.workload)}</b><span>${escapeHtml(row.slice)}</span></button></td><td class="bad">${pct(row.cpi_degradation_percent, 3)}</td><td class="bad">${signed(row.weighted_cpi_contribution, 6)}</td><td title="${escapeHtml(clues)}">${escapeHtml(clues)}</td></tr>`;
    }).join('') : '<tr><td colspan="4" class="anomaly-empty compact">没有超过阈值的 slice</td></tr>';
    return `<article class="anomaly-block"><div class="anomaly-head"><div><span>${label}</span><b>${comparison.baseline.short_sha.slice(0, 7)} → ${comparison.current.short_sha.slice(0, 7)}</b></div><div class="anomaly-count"><strong>${comparison.summary.anomalous_workload_count}</strong> workloads · <strong>${comparison.summary.anomalous_slice_count}</strong> slices</div></div><div class="anomaly-body"><div class="anomaly-workloads"><h3>异常 Workload</h3>${workloadRows}</div><div class="anomaly-slices"><h3>加权影响最大的候选 Slice</h3><div class="table-scroll"><table><thead><tr><th>Workload / Slice</th><th>CPI 退化</th><th>加权 ΔCPI</th><th>计数器线索</th></tr></thead><tbody>${sliceRows}</tbody></table></div></div></div></article>`;
  }).join('');
  $('anomalyComparisons').querySelectorAll('button[data-kind]').forEach(button => button.onclick = () => {
    const base = Number(button.dataset.base), target = Number(button.dataset.target);
    if (base >= 0) state.base = base;
    if (target >= 0) state.target = target;
    state.workload = button.dataset.workload;
    if (button.dataset.kind === 'slice') {
      state.level = 'slice';
      state.slice = button.dataset.slice;
    } else {
      state.level = 'workload';
    }
    render();
    $('slices').scrollIntoView({behavior:'smooth', block:'start'});
  });
}

function renderCommits() {
  const measured = model.measuredCommits || ObservationFilter.measuredCatalogCommits(model.manifest.commits, model.commits);
  $('missingBadge').textContent = `${measured.length} 个提交 · ${model.commits.length} 次测量`;
  $('commitRows').innerHTML = measured.map((commit, index) => {
    const observations = model.commits.filter(row => row.commit_sha === commit.commit_sha && row.run_id);
    const isBase = commit.commit_sha === model.commits[state.base].commit_sha;
    const isTarget = commit.commit_sha === model.commits[state.target].commit_sha;
    const coverage = observations.map(run => `${run.score_coverage?.replace(/c$/, 'cov') || '测量'} · ${run.valid_slices}/${run.expected_slices} ROI · ${run.status === 'published' ? '完整' : '有缺项'}`).join('；');
    return `<tr class="${isTarget ? 'target-row' : ''}"><td>${String(index + 1).padStart(2, '0')}</td><td><code>${commit.short_commit}</code>${isBase ? '<span class="ab-badge">A</span>' : ''}${isTarget ? '<span class="ab-badge target">B</span>' : ''}</td><td>${fullDate(commit.commit_time)}</td><td title="${escapeHtml(commit.subject)}">${escapeHtml(commit.subject)}</td><td><span class="coverage-state">${coverage}</span></td></tr>`;
  }).join('');
}

function renderScoreTrend() {
  const groups = new Map(), palette = ['#2f63db', '#d5732f', '#239b8b', '#8259c7'];
  model.commits.forEach((run, index) => {
    const value = suiteScore(index);
    if (!finite(value)) return;
    const source = model.scoreSources.get(`${state.scoreSuite}\u0000${run.run_id}`);
    const key = `${run.config}\u0000${source}`;
    if (!groups.has(key)) {
      const scope = source.match(/-(\d+(?:\.\d+)?c)\.txt$/)?.[1] || source;
      groups.set(key, {label:`${state.scoreSuite}/GHz · ${run.config} · ${scope}`,
        kind:'score', color:palette[groups.size % palette.length], values:model.commits.map(() => null)});
    }
    groups.get(key).values[index] = value;
  });
  const series = [...groups.values()];
  $('trendTitle').textContent = `${state.scoreSuite}/GHz 分数趋势`;
  $('trendSub').textContent = '显示全部测量，× 表示缺少当前分数；0.3cov 和 1.0cov 分别展示，完整性按各自预定切片判断；悬停查看报告覆盖率；同一配置与评分口径分别连线。原始分数趋势不等同于已核实的回归结论。';
  $('valueMode').hidden = false;
  $('legend').innerHTML = series.map(item => `<span class="legend-item" style="--legend-color:${item.color}"><i class="legend-line"></i>${escapeHtml(item.label)}</span>`).join('');
  drawMultiChart('trend', series, {sharedScale:true, connect:rawTrendCompatible, format:value => value.toFixed(3)});
}

function renderTrend() {
  if (state.level === 'suite' && state.metric === 'score' && state.mode === 'absolute') {
    renderScoreTrend();
    return;
  }
  if (state.level === 'counter' && state.counterDisplay === 'counters' && !selectedCounterMetricIds().length) {
    $('trendTitle').textContent = `${currentName()} · 性能计数器趋势`;
    $('trendSub').textContent = '请至少勾选一个性能计数器';
    $('valueMode').hidden = true;
    $('legend').innerHTML = '';
    $('trend').innerHTML = '<div class="empty-state">未选择性能计数器</div>';
    return;
  }
  if (state.level === 'counter' && state.counterDisplay === 'ipc') {
    const values = model.commits.map((_, index) => ipcAt(state.counterSlice, index));
    $('trendTitle').textContent = `${currentName()} · IPC趋势`;
    $('trendSub').textContent = '横轴为 commit · 固定切片 IPC · 可切换绝对值 / 相对基线';
    $('valueMode').hidden = false;
    const displayed = state.mode === 'relative' ? values.map((_, index) => ipcChangePct(state.counterSlice, state.base, index)) : values;
    $('legend').innerHTML = '<span class="legend-item"><i class="legend-line"></i>IPC</span>';
    drawChart('trend', displayed, {connect:(a,b) => sliceAssessment(state.counterSlice,a,b).allowed, color:'#2f63db', format:state.mode === 'relative' ? value => `${signed(value, 2)}%` : value => value.toFixed(3)});
    return;
  }
  if (state.level === 'counter' && state.counterDisplay !== 'ipc' && (state.counterDisplay === 'combined' || selectedCounterMetricIds().length > 1)) {
    const ids = selectedCounterMetricIds();
    const ipcRaw = model.commits.map((_, index) => ipcAt(state.counterSlice, index));
    const series = [];
    if (state.counterDisplay === 'combined') series.push({label:'IPC', kind:'ipc', color:'#2f63db', values:state.mode === 'absolute' ? ipcRaw : ipcRaw.map((_, index) => ipcChangePct(state.counterSlice, state.base, index))});
    const palette = ['#d5732f','#239b8b','#8259c7','#d14973','#5b84d8','#8c9a3c','#b34e9b','#4d8baf'];
    ids.forEach((metricId, index) => {
      const metric = model.counterMetricById.get(metricId);
      const raw = model.commits.map((_, commitIndex) => counterValue(state.counterSlice, commitIndex, metricId));
      series.push({label:metric.display_name, metricId, kind:'counter', color:palette[index % palette.length], values:state.mode === 'absolute' ? raw : raw.map((_, index) => counterPerformanceChange(state.counterSlice, state.base, index, metricId))});
    });
    $('trendTitle').textContent = `${currentName()} · ${state.counterDisplay === 'combined' ? 'IPC + 性能计数器' : '性能计数器'}趋势`;
    $('trendSub').textContent = `横轴为提交与测量 · ${state.mode === 'relative' ? '相对基线 A，仅展示可比点' : '绝对值'} · 各序列独立纵向缩放 · ${state.counterUnit === 'per_kinst' ? '每千指令' : '原始计数'}`;
    $('valueMode').hidden = false;
    $('legend').innerHTML = series.map(item => `<span class="legend-item" style="--legend-color:${item.color}"><i class="legend-line"></i>${escapeHtml(item.label)}</span>`).join('');
    drawMultiChart('trend', series, {connect:(a,b,item) => item.kind === 'ipc' ? sliceAssessment(state.counterSlice,a,b).allowed : counterAssessment(state.counterSlice,a,b,item.metricId).allowed});
    return;
  }
  $('valueMode').hidden = false;
  const rawValues = absoluteValues();
  const values = displayValues(rawValues);
  const label = metricLabel();
  $('trendTitle').textContent = `${currentName()} · ${label}趋势`;
  $('trendSub').textContent = state.mode === 'relative' ? '相对基线 A，正值统一表示性能改善' : state.level === 'counter' ? `${selectedCounterMetric().category} · ${counterUnitLabel()} · ${selectedCounterMetric().direction === 'lower_is_better' ? '越低越好' : '越高越好'} · final PERF dump` : state.metric === 'score' ? '评分文件发布值；按当前切片集区分编译器和 ISA' : state.metric === 'index' ? '基线 A = 100；每个测量与 A 按共同有效切片重算 IPC，参与范围可能随测量变化' : '按提交时间、测量时间排列；仅连接实验条件已确认的可比点';
  $('legend').innerHTML = `<span class="legend-item"><i class="legend-line"></i>${escapeHtml(currentName())}</span>`;
  const format = state.mode === 'relative' ? value => `${signed(value, 2)}%` : state.level === 'counter' ? (state.counterUnit === 'per_kinst' ? value => value.toFixed(2) : formatCounterValue) : state.metric === 'index' ? value => value.toFixed(2) : value => value.toFixed(3);
  drawChart('trend', values, {format, connect:state.level === 'slice' ? (a,b) => sliceAssessment(state.slice,a,b).allowed
    : state.level === 'workload' ? (a,b) => {
      const pair = workloadAssessment(state.workload,a,b);
      return pair.allowed && (state.mode === 'relative' || state.metric === 'index' || !pair.common);
    } : state.level === 'counter' ? (a,b) => counterAssessment(state.counterSlice,a,b).allowed
    : state.metric === 'score' ? (a,b) => scoreAssessment(a,b).allowed : (a,b) => finite(suiteRatio(a,b))});
}

function setActiveNavigation(sectionId) {
  document.querySelectorAll('.rail-nav .nav').forEach(link => {
    const active = link.getAttribute('href') === `#${sectionId}`;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  });
}

function renderComparisonContext() {
  const a = model.commits[state.base], b = model.commits[state.target];
  $('configName').textContent = b.config;
  const details = run => run.run_id ? `${run.actions_run_number ? `Weekly #${run.actions_run_number} · ` : ''}${run.short_commit} · 测量 ${run.run_id.slice(0, 8)} · ${run.snapshot_at} · ${run.status} · ${run.valid_slices}/${run.expected_slices} ROI · ${run.source_uri}${run.actions_url ? `\n${run.actions_url}` : ''}${run.observed_emulators?.length ? `\n仿真器：${run.observed_emulators.join(', ')}` : ''}${run.ci_failed_points?.length ? `\nCI 失败切片：${run.ci_failed_points.join(', ')}` : ''}` : `${run.short_commit} · 当前切片集无测量`;
  const strict = canCompare();
  const pair = Comparison.runs(a,b);
  const counterMode = state.level === 'counter';
  const indexMode = state.level === 'suite' && state.metric !== 'score';
  const indexRatio = indexMode ? suiteRatio(state.base,state.target) : null;
  const scoped = counterMode ? counterAnalysisSlices() : pairSlices(indexMode ? model.slices : model.slicesByWorkload.get(state.workload) || []);
  const compared = scoped.map(slice => counterMode ? counterAssessment(slice.slice) : sliceAssessment(slice.slice));
  const count = compared.filter(row => row.allowed).length;
  const reasons = [...new Set(compared.flatMap(row => row.reasons))];
  const scoreMode = state.level === 'suite' && state.metric === 'score';
  const current = scoreMode ? scoreAssessment() : indexMode ? {
    allowed:finite(indexRatio), reasons:finite(indexRatio) ? [] : ['无共同可比 Workload'],
    notes:['按共同有效切片重算各 Workload IPC，再取变化比的几何平均']
  } : state.level === 'workload' ? workloadAssessment(state.workload)
    : counterMode ? counterAssessment(state.counterSlice) : sliceAssessment(state.slice);
  const label = scoreMode ? `${state.scoreSuite} 分数` : indexMode ? '共同切片套件 IPC' : state.level === 'workload' ? `${state.workload} IPC`
    : counterMode ? '当前计数器' : '当前切片';
  const partial = a.status === 'partial' || b.status === 'partial';
  $('comparisonNotice').textContent = `${partial ? '部分数据对比' : strict ? '整组可比' : '逐项对比'} · ${label}${current.allowed ? '可比' : '暂无可比值'} · ${counterMode ? '计数器' : indexMode ? '全部切片' : state.workload + ' 切片'} ${count}/${scoped.length}`;
  $('comparisonNotice').dataset.status = current.allowed || count || strict ? 'provisional' : 'blocked';
  const groupReasons = [];
  if (a.status !== 'published' || b.status !== 'published') groupReasons.push('存在部分或未发布运行');
  if (a.comparison_key !== b.comparison_key) groupReasons.push('整组比较键不同');
  if (a.slice_set_id !== b.slice_set_id) groupReasons.push('切片集不同');
  if (!strict && !groupReasons.length) groupReasons.push('整组条件未核实');
  $('comparisonNotice').title = [...current.reasons, ...current.notes, ...groupReasons, ...reasons].join('；');
  $('observationDetails').textContent = `A: ${details(a)}\nB: ${details(b)}\n整组：${strict ? '可比' : groupReasons.join('；')}\n逐项：${count}/${scoped.length} 诊断可比；${reasons.join('；')}\n${pair.notes.join('；')}`;
  $('export').disabled = false;
  if (counterMode) {
    $('sliceCount').textContent = `${model.counterSlices.length} 个切片`;
    $('observationCount').textContent = `${model.manifest.available_counters} / ${model.manifest.counter_count}`;
    $('coverageText').textContent = `${model.manifest.counter_count ? (model.manifest.available_counters / model.manifest.counter_count * 100).toFixed(1) : '—'}% 计数器覆盖`;
  } else {
    $('sliceCount').textContent = `${model.manifest.slice_count} 个`;
    $('observationCount').textContent = `${model.manifest.valid_observations} / ${model.manifest.expected_observations}`;
    $('coverageText').textContent = `${model.manifest.expected_observations ? (model.manifest.valid_observations / model.manifest.expected_observations * 100).toFixed(1) : '—'}% 数据覆盖`;
  }
}

function render() {
  syncControls();
  const navId = state.level === 'counter' ? 'trendPanel' : state.level === 'suite' ? 'top' : state.level === 'workload' ? 'workloads' : 'slices';
  setActiveNavigation(navId);
  renderComparisonContext();
  const counterMode = state.level === 'counter';
  $('workloads').hidden = counterMode;
  $('trendScale').hidden = !counterMode || state.counterDisplay === 'ipc' || (state.counterDisplay === 'counters' && selectedCounterMetricIds().length < 2);
  $('counterInsights').hidden = !counterMode;
  if (!counterMode) {
    $('valueAHead').textContent = 'IPC · A';
    $('valueBHead').textContent = 'IPC · B';
    $('changeHead').textContent = 'IPC 变化';
    $('impactHead').textContent = '加权 ΔCPI';
    $('impactHead').title = '切片权重 × (CPI B − CPI A)，正值表示退化贡献';
    $('sort').disabled = false;
  }
  renderKpis();
  renderAnomalies();
  renderTrend();
  if (counterMode) {
    renderRelation();
    renderCorrelationBars();
    renderCounterMatrix();
  }
  if (!counterMode) {
    renderWorkloads();
  }
  renderSlices();
  renderSliceCounters();
  renderTransitions();
  renderCommits();
}

function bindEvents() {
  document.querySelectorAll('#workloadFilters [data-filter]').forEach(button => button.onclick = () => {
    state.workloadFilter = button.dataset.filter;
    renderWorkloads();
  });
  $('workloadSearch').oninput = event => {
    state.workloadSearch = event.target.value;
    renderWorkloads();
  };
  $('scoreSuite').onchange = event => { state.scoreSuite = event.target.value; render(); };
  $('dataset').onchange = event => {
    const url = new URL(location.href);
    url.searchParams.set('dataset', event.target.value);
    location.href = url.href;
  };
  $('level').onchange = event => {
    state.level = event.target.value;
    if (state.level === 'suite') state.metric = 'score';
    if (state.level === 'slice' && !state.slice) state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
  };
  $('entity').onchange = event => {
    if (state.level === 'workload') {
      state.workload = event.target.value;
      state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    } else if (state.level === 'slice') {
      state.slice = state.counterSlice = event.target.value;
      state.workload = model.slices.find(slice => slice.slice === state.slice).workload;
    } else if (state.level === 'counter') {
      state.counterSlice = event.target.value;
    }
    render();
  };
  $('metric').onchange = event => {
    if (state.level === 'counter') {
      state.counterMetric = event.target.value;
      if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    }
    else state.metric = event.target.value;
    render();
  };
  $('base').onchange = event => { state.base = Number(event.target.value); render(); };
  $('target').onchange = event => { state.target = Number(event.target.value); render(); };
  $('sort').onchange = event => { state.sort = event.target.value; renderSlices(); };
  $('counterUnit').onchange = $('sliceCounterUnit').onchange = event => { state.counterUnit = event.target.value; render(); };
  document.querySelectorAll('#counterDisplayMode [data-display]').forEach(button => button.onclick = () => {
    state.counterDisplay = button.dataset.display;
    render();
  });
  $('selectAllCounters').onclick = () => { state.selectedCounterMetrics = model.counterMetrics.map(metric => metric.metric_id); render(); };
  $('clearCounters').onclick = () => { state.selectedCounterMetrics = []; state.counterDisplay = 'ipc'; render(); };
  document.querySelectorAll('#relationMode [data-rel]').forEach(button => button.onclick = () => { state.relationMode = button.dataset.rel; render(); });
  document.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => { state.mode = button.dataset.mode; render(); });
  $('reset').onclick = () => {
    state = {
      ...state, ...defaultComparison(),
      level:'suite', metric:'score', mode:'absolute', sort:'impact',
      counterDisplay:'combined', selectedCounterMetrics:[state.counterMetric],
      workloadFilter:'all', workloadSearch:''
    };
    $('workloadSearch').value = '';
    render();
  };
  $('export').onclick = exportComparison;
  document.querySelectorAll('.rail-nav .nav[href^="#"]').forEach(link => link.onclick = event => {
    event.preventDefault();
    const id = link.getAttribute('href').slice(1);
    if (link.id === 'counterNav') {
      state.level = 'counter';
      state.mode = 'absolute';
      render();
    } else if (id === 'top') {
      state.level = 'suite';
      state.metric = 'score';
      render();
    } else if (id === 'workloads' && state.level === 'counter') {
      state.level = 'workload';
      state.metric = 'ipc';
      render();
    }
    const section = $(id);
    if (section.tagName === 'DETAILS') section.open = true;
    setActiveNavigation(id);
    history.replaceState(null, '', `#${id}`);
    section.scrollIntoView({behavior:'smooth', block:'start'});
  });
  const initialSection = document.getElementById(location.hash.slice(1));
  if (initialSection?.tagName === 'DETAILS') initialSection.open = true;
}

function csvCell(value) {
  const text = value == null ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCsv(header, rows, filename) {
  const blob = new Blob(['\ufeff' + [header.join(','), ...rows].join('\n')], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportComparison() {
  if (state.level === 'counter') {
    exportCounterComparison();
    return;
  }
  const header = ['baseline','target','workload','slice','checkpoint','weight','ipc_a','ipc_b','ipc_change_pct','weighted_cpi_delta','comparison_status','score_coverage','comparison_reasons'];
  const rows = pairSlices().map(slice => {
    const a = sliceIPC(slice, state.base), b = sliceIPC(slice, state.target);
    const delta = ipcChangePct(slice.slice, state.base, state.target);
    const impact = sliceImpact(slice.slice);
    const assessment = sliceAssessment(slice.slice);
    return [model.commits[state.base].short_commit, model.commits[state.target].short_commit, slice.workload, sliceName(slice.slice), slice.checkpoint, slice.weight, a, b, delta, impact, assessment.mode, model.commits[state.target].score_coverage, [...assessment.reasons,...assessment.notes].join('; ')].map(csvCell).join(',');
  });
  downloadCsv(header, rows, `spec06-ipc-${model.commits[state.base].short_commit}-to-${model.commits[state.target].short_commit}.csv`);
}

function exportCounterComparison() {
  const metric = selectedCounterMetric();
  const {rho} = spearman(relationPairs(state.relationMode));
  const header = ['baseline','target','metric_id','metric_name','unit','unit_mode','direction','relation_mode','spearman_rho','slice','checkpoint','weight','value_a','value_b','performance_change_pct','ipc_a','ipc_b','ipc_change_pct','availability_a','availability_b','comparison_status','score_coverage','comparison_reasons'];
  const rows = pairSlices(model.counterSlices).map(slice => {
    const assessment = counterAssessment(slice.slice);
    const rowA = counterRow(slice.slice, state.base), rowB = counterRow(slice.slice, state.target);
    const a = counterValue(slice.slice, state.base), b = counterValue(slice.slice, state.target);
    const ipcA = ipcAt(slice.slice, state.base), ipcB = ipcAt(slice.slice, state.target);
    return [model.commits[state.base].short_commit, model.commits[state.target].short_commit, metric.metric_id, metric.display_name, counterUnitLabel(metric), state.counterUnit, metric.direction, state.relationMode, rho, sliceName(slice.slice), slice.checkpoint, slice.weight, a, b, counterPerformanceChange(slice.slice), ipcA, ipcB, ipcChangePct(slice.slice,state.base,state.target), rowA?.availability, rowB?.availability, assessment.mode, model.commits[state.target].score_coverage, [...assessment.reasons,...assessment.notes].join('; ')].map(csvCell).join(',');
  });
  downloadCsv(header, rows, `perf-counter-${metric.metric_id}-${model.commits[state.base].short_commit}-to-${model.commits[state.target].short_commit}.csv`);
}

async function init() {
  try {
    if (await loadData() === false) return;
    const firstDate = shortDate(model.commits[0].commit_time), lastDate = shortDate(model.commits.at(-1).commit_time);
    $('scopeStats').textContent = `${model.measuredCommits.length} 个提交 · ${model.commits.filter(run => run.status === 'published').length} 次完整 · ${model.commits.filter(run => run.status === 'partial').length} 次部分数据`;
    $('scopeRange').textContent = `${firstDate} – ${lastDate}`;
    if (!model.counterMetrics.length) {
      $('counterNav').hidden = true;
      $('level').querySelector('option[value="counter"]').remove();
    }
    document.title = '香山 · 主线性能看板';
    document.querySelector('.version').textContent = model.manifest.suite;
    bindEvents();
    render();
    $('loading').hidden = true;
    $('dashboard').hidden = false;
    const initialSection = document.getElementById(location.hash.slice(1));
    if (initialSection) {
      setActiveNavigation(initialSection.id);
      initialSection.scrollIntoView({block:'start'});
    }
  } catch (error) {
    console.error(error);
    $('loading').hidden = true;
    $('error').hidden = false;
    $('error').innerHTML = `<strong>数据加载失败</strong><span>${escapeHtml(error.message)}。请通过本地 HTTP 服务打开看板，并确认数据目录可访问。</span>`;
  }
}

init();
