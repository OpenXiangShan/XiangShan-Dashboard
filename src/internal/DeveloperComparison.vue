<template>
  <div class="developer-comparison">
    <section class="analysis-launch" aria-label="RTL / GEM5 开发者分析">
      <div class="launch-heading">
        <span class="internal-tag">内网</span
        ><strong>RTL / GEM5 详细分析</strong>
        <span class="muted">从 CI 的原始 stats 按需提取</span>
        <button
          v-if="result"
          class="text-button"
          @click="showDetails = !showDetails"
        >
          {{ showDetails ? "返回评分对比" : "查看详细结果" }}
        </button>
      </div>
      <div class="launch-fields">
        <label
          >Baseline A<input
            v-model="baselineInput"
            class="control-input"
            :placeholder="baselinePlaceholder"
            :disabled="busy"
            aria-label="Baseline CI 链接或 ID"
        /></label>
        <label
          >对比 CI B<input
            v-model="targetInput"
            class="control-input"
            :placeholder="targetPlaceholder"
            :disabled="busy"
            aria-label="对比 CI 链接或 ID"
            @keydown.enter="start"
        /></label>
        <button class="primary" :disabled="busy" @click="start">
          {{ busy ? "正在分析…" : "详细分析" }}
        </button>
      </div>
      <div class="launch-note muted">
        A 留空使用左侧已选运行；B 可输入 XiangShan / GEM5 CI 链接或 Run ID。
      </div>
      <div
        v-if="candidates[0].length > 1 || candidates[1].length > 1"
        class="archive-choices"
      >
        <label v-for="(choices, index) in candidates" :key="index"
          >{{ index === 0 ? "A" : "B" }} 归档配置
          <select
            v-model="archiveIds[index]"
            class="control-input"
            :disabled="busy"
          >
            <option value="">请选择配置 / 数据集</option>
            <option
              v-for="archive in choices"
              :key="archive.id"
              :value="archive.id"
              :disabled="!archive.available"
            >
              {{ archive.label }}
            </option>
          </select>
        </label>
      </div>
      <div v-if="busy" class="progress" role="status">
        <span class="spinner" />{{ job?.stage || "定位 NFS 归档…"
        }}<span v-if="job?.queue_position">
          · 队列第 {{ job.queue_position }} 位</span
        >
        <span v-if="job?.elapsed_seconds" class="elapsed"
          >已用时 {{ job.elapsed_seconds }} 秒</span
        >
        <button
          v-if="job && ['queued', 'running'].includes(job.status)"
          class="stop-button"
          :disabled="stopping"
          @click="stopAnalysis"
        >
          {{ stopping ? "正在停止…" : "停止分析" }}
        </button>
      </div>
      <div class="launch-note muted">
        刷新或关闭页面不会停止后台任务；请使用“停止分析”。
      </div>
      <div v-if="notice" class="notice" role="status">{{ notice }}</div>
      <div v-if="error" class="error" role="alert">{{ error }}</div>
    </section>

    <div v-if="!showDetails || !result" class="original-comparison">
      <slot />
    </div>
    <section v-else class="analysis-results">
      <div class="result-heading">
        <div>
          <h2>性能计数器对比</h2>
          <span class="muted"
            >{{
              result.query.level === "benchmark"
                ? "Benchmark 加权结果"
                : "SimPoint 测量窗口"
            }}
            · {{ result.row_count }} 行</span
          >
        </div>
        <a
          class="export-link"
          :href="`/api/analysis/jobs/${completedJobId}/rows.csv`"
          >导出当前 CSV ↗</a
        >
      </div>
      <div class="run-cards">
        <article
          v-for="(run, index) in result.runs"
          :key="run.snapshot_id + index"
        >
          <div class="run-title">
            <span class="run-badge" :class="{ target: index === 1 }">{{
              index === 0 ? "A" : "B"
            }}</span
            ><a
              :href="
                run.actions_url ||
                `https://github.com/OpenXiangShan/${run.repository || 'GEM5'}/actions/runs/${run.run_id}`
              "
              target="_blank"
              rel="noreferrer"
              >Run {{ run.run_id }} ↗</a
            ><code>{{ run.commit.slice(0, 10) }}</code>
          </div>
          <div class="run-config">
            {{ run.benchmark_type }} · {{ run.config }}
          </div>
          <div class="run-stats">
            <span
              >完成切片
              <strong
                >{{ run.completed_points }}/{{ run.expected_points }}</strong
              ></span
            ><span
              >原始运行分数/GHz
              <strong>{{ number(summaryScore(run)) }}</strong></span
            >
          </div>
          <details>
            <summary>来源与处理版本</summary>
            <code>{{ run.path }}</code>
            <p>
              profile {{ run.profile_hash.slice(0, 12) }} · processor
              {{ run.processor_hash.slice(0, 12) }}
            </p>
          </details>
        </article>
      </div>
      <div v-if="result.warnings.length" class="warnings">
        <div v-for="warning in result.warnings" :key="warning">
          {{ warning }}
        </div>
      </div>
      <div class="query-grid">
        <div class="metric-picker">
          <label class="field-label"
            >搜索计数器
            <span class="muted">YAML 别名 / stats 原始名称</span></label
          >
          <input
            v-model="search"
            class="control-input"
            placeholder="例如 cpi、branchPred、miss、新增计数器名称"
            aria-label="搜索计数器"
          />
          <div class="group-chips">
            <button
              :class="{ active: groups.length === 0 }"
              @click="clearGroups"
            >
              全部</button
            ><button
              v-for="group in metricGroups"
              :key="group"
              :class="{ active: groups.includes(group) }"
              @click="toggleGroup(group)"
            >
              {{ group }}
            </button>
          </div>
          <div class="metric-options" aria-label="计数器搜索结果">
            <label v-for="metric in metricOptions" :key="metric.id"
              ><input
                type="checkbox"
                :checked="metrics.includes(metric.id)"
                @change="toggleMetric(metric.id)"
              /><span :title="metric.name">{{ metric.name }}</span
              ><small>{{
                metric.kind === "raw" ? "原始" : "YAML"
              }}</small></label
            >
            <span v-if="!metricOptions.length" class="muted">{{
              metricLoading ? "搜索中…" : "没有匹配的计数器"
            }}</span>
          </div>
          <div class="muted metric-count">
            匹配 {{ metricTotal }} 项，显示前 100 项；已选
            {{ metrics.length }} 项。分类会自动加入/移除全部指标。
          </div>
          <div class="selected-metrics">
            <button
              v-for="metric in metrics"
              :key="metric"
              @click="toggleMetric(metric)"
              :title="metric"
            >
              {{ metric.replace(/^raw:/, "") }} ×
            </button>
          </div>
        </div>
        <div class="query-controls">
          <label
            >比较层级<select v-model="level" class="control-input">
              <option value="benchmark">Benchmark · SimPoint 加权</option>
              <option value="simpoint">SimPoint · 原始测量</option>
            </select></label
          >
          <label
            >Benchmark<select v-model="benchmark" class="control-input">
              <option value="">全部 Benchmark</option>
              <option v-for="name in result.benchmarks" :key="name">
                {{ name }}
              </option>
            </select></label
          >
          <label v-if="level === 'simpoint'"
            >SimPoint<select v-model="simpoint" class="control-input">
              <option value="">全部 SimPoint</option>
              <option v-for="name in visibleSimpoints" :key="name">
                {{ name }}
              </option>
            </select></label
          >
          <div class="sort-fields">
            <label
              >排序<select v-model="sort" class="control-input">
                <option value="abs_pct">变化幅度 ↓</option>
                <option value="pct">百分比变化 ↓</option>
                <option value="abs_delta">绝对差值 |B − A| ↓</option>
                <option value="delta">差值 B − A ↓</option>
                <option value="name">名称</option>
              </select></label
            ><label
              >|变化| ≥ %<input
                v-model.number="minChange"
                type="number"
                min="0"
                step="0.1"
                class="control-input"
            /></label>
          </div>
          <button
            class="primary query-button"
            :disabled="busy || !metrics.length"
            @click="runQuery"
          >
            {{ busy ? "查询中…" : "应用筛选并查询" }}
          </button>
          <span class="muted"
            >筛选不改变顶部原始分数。缺失值显示为 —；A=0 时不计算百分比。</span
          >
          <span
            v-if="metrics.some((metric) => metric.startsWith('raw:'))"
            class="muted"
          >
            {{
              level === "simpoint"
                ? "raw 直接读取所选切片的测量值，不加权；选择单个 SimPoint 查询最快。"
                : "raw 的 Benchmark 查询需要读取切片并加权；只看原始值可切换 SimPoint 并选择切片。"
            }}
          </span>
        </div>
      </div>
      <div v-if="dirty" class="pending-note">
        筛选条件已更改，点击“应用筛选并查询”更新图表和表格。
      </div>
      <section class="chart-card">
        <div class="chart-heading">
          <strong>{{
            chartView === "topdown" && hasTopdown
              ? "Intel Topdown L1"
              : "计数器变化"
          }}</strong>
          <select
            v-model="chartView"
            class="control-input"
            aria-label="图表视图"
          >
            <option value="changes">变化概览</option>
            <option value="metric">A/B 单指标</option>
            <option v-if="hasTopdown" value="topdown">Intel Topdown L1</option>
          </select>
          <select
            v-if="chartView === 'metric'"
            v-model="chartMetric"
            class="control-input"
            aria-label="图表计数器"
          >
            <option
              v-for="metric in result.query.metrics"
              :key="metric"
              :value="metric"
            >
              {{ metric.replace(/^raw:/, "") }}
            </option></select
          ><span class="muted">{{
            chartView === "topdown" && hasTopdown
              ? "当前结果前 8 项；切片占比加权均值；份额变化不等于周期变化"
              : "当前排序前 15 项；增减不自动代表好坏"
          }}</span>
        </div>
        <div
          v-if="topdownMessages.length"
          class="topdown-diagnostics"
          role="status"
        >
          <div v-for="message in topdownMessages" :key="message">
            {{ message }}
          </div>
        </div>
        <div class="chart-container">
          <canvas
            ref="chartCanvas"
            aria-label="性能计数器 A/B 柱状图"
            role="img"
          />
        </div>
      </section>
      <div class="table-heading">
        <strong>计数器明细</strong
        ><span class="muted">Δ = B − A；变化本身不自动代表好坏</span
        ><span class="table-page"
          ><button :disabled="page === 0" @click="page--">上一页</button
          >{{ page + 1 }} /
          {{ Math.max(1, Math.ceil(result.rows.length / pageSize))
          }}<button
            :disabled="(page + 1) * pageSize >= result.rows.length"
            @click="page++"
          >
            下一页
          </button></span
        >
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>
                {{
                  result.query.level === "simpoint" ? "SimPoint" : "Benchmark"
                }}
              </th>
              <th>计数器</th>
              <th>A</th>
              <th>B</th>
              <th>Δ</th>
              <th>变化 %</th>
              <th>
                {{
                  result.query.level === "simpoint"
                    ? "切片权重 A / B"
                    : "profile 覆盖 A / B"
                }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.name + row.metric">
              <td>{{ row.name }}</td>
              <td class="metric-name" :title="row.metric">
                {{ row.metric.replace(/^raw:/, "") }}
              </td>
              <td>{{ number(row.a) }}</td>
              <td>{{ number(row.b) }}</td>
              <td>{{ number(row.delta, true) }}</td>
              <td
                :class="{
                  positive: (row.delta_pct || 0) > 0,
                  negative: (row.delta_pct || 0) < 0,
                }"
                :title="row.status"
              >
                {{ percent(row.delta_pct) }}
              </td>
              <td>
                {{
                  percent(
                    (result.query.level === "simpoint"
                      ? row.weight_a
                      : row.coverage_a) == null
                      ? null
                      : (result.query.level === "simpoint"
                          ? row.weight_a!
                          : row.coverage_a!) * 100,
                    false,
                  )
                }}
                /
                {{
                  percent(
                    (result.query.level === "simpoint"
                      ? row.weight_b
                      : row.coverage_b) == null
                      ? null
                      : (result.query.level === "simpoint"
                          ? row.weight_b!
                          : row.coverage_b!) * 100,
                    false,
                  )
                }}
              </td>
            </tr>
            <tr v-if="!result.rows.length">
              <td colspan="7" class="empty-result">
                筛选后没有数据，请调整计数器或变化阈值。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import {
  Chart,
  BarController,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
} from "chart.js";
import { MAX_METRICS, toggleMetricGroup } from "./metricSelection";
import {
  DEFAULT_METRICS,
  TOPDOWN_METRICS,
  TOPDOWN_LABELS,
  topdownDiagnostics,
} from "./topdown";
import type { ComparisonSource } from "../types/comparison";
import {
  api,
  type Archive,
  type Job,
  type Metric,
  type Query,
  type Result,
  type RunSnapshot,
} from "./analysisApi";
Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
);
const props = defineProps<{ sources: ComparisonSource[] }>();
const emit = defineEmits<{
  (event: "detailsChange", visible: boolean): void;
}>();
const url = new URLSearchParams(location.search);
const baselineInput = ref(url.get("a") || "");
const targetInput = ref(url.get("b") || "");
const baselinePlaceholder = computed(() =>
  props.sources[0]?.runId && props.sources[0].runId !== "custom"
    ? `已选 Run ${props.sources[0].runId}`
    : "请选择 RTL / GEM5 baseline，或输入 Run ID",
);
const targetPlaceholder = computed(() =>
  props.sources[1]?.runId && props.sources[1].runId !== "custom"
    ? `CI 链接 / Run ID（默认 ${props.sources[1].runId}）`
    : "https://github.com/OpenXiangShan/GEM5/actions/runs/…",
);
const candidates = ref<Archive[][]>([[], []]);
const archiveIds = ref(["", ""]);
const resolvedInputs = ref("");
const stopping = ref(false),
  notice = ref("");
const busy = ref(false),
  error = ref(""),
  showDetails = ref(false);
const job = ref<Job | null>(null),
  result = ref<Result | null>(null),
  completedJobId = ref("");
const metrics = ref([...DEFAULT_METRICS]),
  groups = ref<string[]>([]),
  search = ref("");
const groupMetrics = ref<Record<string, string[]>>({});
const metricOptions = ref<Metric[]>([]),
  metricGroups = ref<string[]>([]),
  metricTotal = ref(0),
  metricLoading = ref(false);
const level = ref<"benchmark" | "simpoint">("benchmark"),
  benchmark = ref(""),
  simpoint = ref("");
const sort = ref("abs_delta"),
  minChange = ref(0),
  chartMetric = ref("cpi");
const chartView = ref<"changes" | "metric" | "topdown">("topdown");
const topdownMetrics = TOPDOWN_METRICS;
const hasTopdown = computed(() =>
  topdownMetrics.every((metric) =>
    result.value?.query.metrics.includes(metric),
  ),
);
const topdownMessages = computed(() => {
  if (!result.value || chartView.value !== "topdown" || !hasTopdown.value)
    return [];
  return result.value.runs.flatMap((run, i) => {
    const side = i === 0 ? "a" : "b";
    const label = `${i === 0 ? "A" : "B"} · ${run.backend === "xs" ? "RTL" : "GEM5"}`;
    const messages = topdownDiagnostics(result.value!.rows, side);
    if (
      run.backend === "xs" &&
      result.value!.rows.some(
        (row) => row.metric === "badSpecBound" && row[side] == null,
      )
    )
      messages.push(
        "RTL 旧公式依赖 inst_spec / recovery_bubble；日志未导出时无法还原 Bad Speculation 和 Backend Bound，recovery_stall 不能替代。",
      );
    return messages.map((message) => `${label}：${message}`);
  });
});
const page = ref(0),
  pageSize = 100;
const visibleRows = computed(
  () =>
    result.value?.rows.slice(
      page.value * pageSize,
      (page.value + 1) * pageSize,
    ) || [],
);
const visibleSimpoints = computed(
  () =>
    result.value?.simpoints.filter(
      (n) =>
        !benchmark.value ||
        n === benchmark.value ||
        n.startsWith(benchmark.value + "_"),
    ) || [],
);
const dirty = computed(
  () =>
    !!result.value &&
    JSON.stringify(parameters()) !== JSON.stringify(result.value.query),
);
let generation = 0,
  metricGeneration = 0;
let searchTimer: ReturnType<typeof setTimeout> | undefined;
let chart: Chart | undefined;
const chartCanvas = ref<HTMLCanvasElement | null>(null);
function number(value: number | null, signed = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  const text =
    Math.abs(value) >= 1e7 || (value !== 0 && Math.abs(value) < 1e-4)
      ? value.toExponential(3)
      : value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return signed && value > 0 ? "+" + text : text;
}
function percent(value: number | null, signed = true) {
  return value == null
    ? "—"
    : `${signed && value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
function summaryScore(run: RunSnapshot) {
  return (
    run.scores.find((s) => s.name === "overall_avg")?.score ??
    run.scores.find((s) => s.name === "int_avg")?.score ??
    null
  );
}
function preferred(choices: Archive[], source?: ComparisonSource) {
  const available = choices.filter((c) => c.available);
  if (available.length === 1) return available[0].id;
  const subset = source?.dataset?.subset || "";
  const variant = subset.split("/")[0];
  const coverage = source?.dataset?.tab.coverage;
  const matching = available.filter(
    (c) =>
      (!coverage || c.benchmark_type.endsWith(coverage)) &&
      (variant === "ideal"
        ? ["ideal", "idealkmhv3"].includes(c.config)
        : variant === "default"
          ? c.config === "kmhv3" || c.backend === "xs"
          : false),
  );
  return matching.length === 1 ? matching[0].id : "";
}
async function start() {
  error.value = "";
  const inputs = [
    baselineInput.value.trim() || props.sources[0]?.runId || "",
    targetInput.value.trim() || props.sources[1]?.runId || "",
  ];
  if (!inputs[0] || !inputs[1] || inputs.includes("custom")) {
    error.value =
      "请选择已有 RTL / GEM5 baseline，或输入两个有效 Run ID / CI 链接。";
    return;
  }
  busy.value = true;
  try {
    const key = JSON.stringify(inputs);
    if (key !== resolvedInputs.value) {
      const resolved = await Promise.all(
        inputs.map((run, index) => {
          const params = new URLSearchParams({ run });
          // A numeric ID has no repository; only inherit the dataset for empty inputs.
          const explicit =
            index === 0 ? baselineInput.value.trim() : targetInput.value.trim();
          const branch = props.sources[index]?.dataset?.branch || "";
          if (
            !explicit &&
            (branch.startsWith("xs/") || branch.startsWith("gem5/"))
          ) {
            params.set("backend", branch.startsWith("xs/") ? "xs" : "gem5");
          }
          return api<{ candidates: Archive[] }>(`resolve?${params}`);
        }),
      );
      candidates.value = resolved.map((r) => r.candidates);
      archiveIds.value = candidates.value.map((choices, i) =>
        preferred(choices, props.sources[i]),
      );
      resolvedInputs.value = key;
    }
    if (archiveIds.value.some((id) => !id)) {
      error.value =
        "一个 Run 包含多份性能归档，请选择 A/B 的配置后再次点击详细分析。";
      return;
    }
    await submit();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}
function parameters(): Query {
  return {
    a: archiveIds.value[0],
    b: archiveIds.value[1],
    metrics: metrics.value,
    groups: groups.value,
    level: level.value,
    benchmarks: benchmark.value ? [benchmark.value] : [],
    simpoints:
      level.value === "simpoint" && simpoint.value ? [simpoint.value] : [],
    sort: sort.value,
    min_change: minChange.value || 0,
  };
}
async function stopAnalysis() {
  if (!job.value || stopping.value) return;
  stopping.value = true;
  try {
    const cancelled = await api<Job>(`jobs/${job.value.id}/cancel`, {});
    if (cancelled.id === job.value?.id) job.value = cancelled;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    stopping.value = false;
  }
}
async function submit() {
  const token = ++generation;
  notice.value = "";
  job.value = null;
  const active = await api<Job>("compare", parameters());
  sessionStorage.setItem("gem5-analysis-job", active.id);
  await followJob(active, token);
}
async function followJob(active: Job, token: number) {
  job.value = active;
  while (
    token === generation &&
    ["queued", "running"].includes(active.status)
  ) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    if (token !== generation) return;
    const update: Job = await api<Job>(`jobs/${active.id}`);
    if (token !== generation) return;
    active = update;
    job.value = active;
  }
  if (token !== generation) return;
  sessionStorage.removeItem("gem5-analysis-job");
  if (active.status === "cancelled") {
    notice.value = "分析已停止，可以调整运行后重新开始。";
    return;
  }
  if (active.status === "failed") throw new Error(active.error || "分析失败");
  result.value = active.result!;
  completedJobId.value = active.id;
  if (chartView.value === "topdown" && !hasTopdown.value)
    chartView.value = "changes";
  showDetails.value = true;
  page.value = 0;
  if (!result.value.query.metrics.includes(chartMetric.value))
    chartMetric.value = result.value.query.metrics[0];
  await loadMetrics();
  await renderChart();
}
async function runQuery() {
  busy.value = true;
  error.value = "";
  try {
    await submit();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}
async function loadMetrics() {
  if (!result.value) return;
  const token = ++metricGeneration;
  const params = new URLSearchParams({ q: search.value });
  result.value.runs.forEach((run) =>
    params.append("snapshot", run.snapshot_id),
  );
  groups.value.forEach((group) => params.append("group", group));
  metricLoading.value = true;
  try {
    const catalog = await api<{
      items: Metric[];
      groups: string[];
      group_metrics: Record<string, string[]>;
      total: number;
    }>(`metrics?${params}`);
    if (token !== metricGeneration) return;
    metricOptions.value = catalog.items;
    metricGroups.value = catalog.groups;
    groupMetrics.value = catalog.group_metrics;
    metricTotal.value = catalog.total;
  } catch (err) {
    if (token === metricGeneration) error.value = String(err);
  } finally {
    if (token === metricGeneration) metricLoading.value = false;
  }
}
function toggleMetric(id: string) {
  if (metrics.value.includes(id))
    metrics.value = metrics.value.filter((m) => m !== id);
  else if (metrics.value.length < MAX_METRICS) metrics.value.push(id);
  else error.value = `每次最多选择 ${MAX_METRICS} 个计数器`;
}
function toggleGroup(group: string) {
  const next = toggleMetricGroup(
    metrics.value,
    groups.value,
    group,
    groupMetrics.value,
  );
  if (next.metrics.length > MAX_METRICS) {
    error.value = `分类合计超过 ${MAX_METRICS} 个计数器，请先取消其他分类`;
    return;
  }
  groups.value = next.groups;
  metrics.value = next.metrics;
  if (group === "intel_topdown" && next.groups.includes(group))
    chartView.value = "topdown";
  else chartView.value = "changes";
}
function clearGroups() {
  for (const group of [...groups.value]) {
    const next = toggleMetricGroup(
      metrics.value,
      groups.value,
      group,
      groupMetrics.value,
    );
    metrics.value = next.metrics;
    groups.value = next.groups;
  }
}
async function renderChart() {
  await nextTick();
  chart?.destroy();
  chart = undefined;
  if (!chartCanvas.value || !result.value || !showDetails.value) return;
  if (chartView.value === "topdown" && hasTopdown.value) {
    const rows = result.value.rows;
    const names = [...new Set(rows.map((row) => row.name))].slice(0, 8);
    const index = new Map(
      rows.map((row) => [`${row.name}:${row.metric}`, row]),
    );
    const colors = ["#4c6ef5", "#d6a43a", "#bf6473", "#20a394"];
    chart = new Chart(chartCanvas.value, {
      type: "bar",
      data: {
        labels: names.flatMap((name) => [`${name} · A`, `${name} · B`]),
        datasets: topdownMetrics.map((metric, i) => ({
          label: TOPDOWN_LABELS[i],
          backgroundColor: colors[i],
          data: names.flatMap((name) => {
            const row = index.get(`${name}:${metric}`);
            return [
              row?.a == null ? null : row.a * 100,
              row?.b == null ? null : row.b * 100,
            ];
          }),
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        scales: {
          x: { stacked: true },
          y: {
            stacked: true,
            beginAtZero: true,
            suggestedMax: 100,
            title: { display: true, text: "切片占比加权均值 (%)" },
          },
        },
      },
    });
    return;
  }
  if (chartView.value !== "metric") {
    const rows = result.value.rows
      .filter((row) => row.delta_pct != null)
      .slice(0, 15);
    const single = new Set(rows.map((row) => row.name)).size === 1;
    chart = new Chart(chartCanvas.value, {
      type: "bar",
      data: {
        labels: rows.map((row) =>
          single
            ? row.metric.replace(/^raw:/, "")
            : `${row.name} · ${row.metric.replace(/^raw:/, "")}`,
        ),
        datasets: [
          {
            label: "B 相对 A 的变化 (%)",
            data: rows.map((row) => row.delta_pct),
            backgroundColor: rows.map((row) =>
              (row.delta_pct || 0) >= 0 ? "#4c6ef5" : "#c58a4a",
            ),
            borderRadius: 3,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: { x: { title: { display: true, text: "相对变化 (%)" } } },
      },
    });
    return;
  }
  const rows = result.value.rows
    .filter((r) => r.metric === chartMetric.value)
    .slice(0, 15);
  chart = new Chart(chartCanvas.value, {
    type: "bar",
    data: {
      labels: rows.map((r) => r.name),
      datasets: [
        {
          label: "A · Baseline",
          data: rows.map((r) => r.a),
          backgroundColor: "#4c6ef5",
          borderRadius: 3,
        },
        {
          label: "B · 对比 CI",
          data: rows.map((r) => r.b),
          backgroundColor: "#20a394",
          borderRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { position: "top" } },
      scales: {
        y: { beginAtZero: true },
        x: { ticks: { maxRotation: 40, minRotation: 0 } },
      },
    },
  });
}
watch([search, groups], () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadMetrics, 250);
});
watch([chartMetric, chartView, showDetails], renderChart);
watch(showDetails, (value) => emit("detailsChange", value));
watch(benchmark, () => {
  simpoint.value = "";
});
watch(
  () => props.sources.map((s) => `${s.dataset?.id}:${s.runId}`).join("|"),
  () => {
    if (!busy.value) {
      resolvedInputs.value = "";
      candidates.value = [[], []];
    }
  },
);
onMounted(async () => {
  const saved = sessionStorage.getItem("gem5-analysis-job");
  if (!saved) return;
  busy.value = true;
  try {
    const active = await api<Job>(`jobs/${saved}`);
    if (active.query) {
      const query = active.query;
      archiveIds.value = [query.a, query.b];
      metrics.value = query.metrics;
      groups.value = query.groups || [];
      level.value = query.level;
      benchmark.value = query.benchmarks?.[0] || "";
      simpoint.value = query.simpoints?.[0] || "";
      sort.value = query.sort;
      minChange.value = query.min_change;
    }
    await followJob(active, ++generation);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    sessionStorage.removeItem("gem5-analysis-job");
  } finally {
    busy.value = false;
  }
});
onBeforeUnmount(() => {
  generation++;
  metricGeneration++;
  clearTimeout(searchTimer);
  chart?.destroy();
});
</script>

<style scoped>
.control-input {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 9px 11px;
  border: 1px solid #dce3f0;
  border-radius: 7px;
  background: #fcfdff;
  color: #24354c;
  font: inherit;
  font-size: 12px;
  min-height: 36px;
}
.control-input:focus {
  outline: 2px solid #cbd9ff;
  outline-offset: 1px;
}
.developer-comparison {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  min-height: 0;
  height: 100%;
}
.analysis-launch {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  flex: none;
}
.launch-heading {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 14px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.internal-tag {
  background: #eaf0ff;
  color: #3659ba;
  padding: 3px 7px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 700;
}
.launch-fields {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(200px, 1.4fr) auto;
  gap: 12px;
  align-items: end;
}
.launch-fields label,
.archive-choices label,
.query-controls label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #536176;
  min-width: 0;
}
.launch-note {
  font-size: 11px;
  margin-top: 8px;
}
.primary {
  border: 0;
  background: #375bd2;
  color: #fff;
  font-weight: 700;
  border-radius: 7px;
  padding: 11px 18px;
  cursor: pointer;
  white-space: nowrap;
}
.primary:disabled {
  opacity: 0.55;
  cursor: wait;
}
.text-button {
  margin-left: auto;
  color: #375bd2;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-weight: 600;
}
.archive-choices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}
.progress {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #eef3ff;
  padding: 10px 12px;
  border-radius: 7px;
  margin-top: 12px;
  font-size: 13px;
  color: #3558bd;
}
.stop-button {
  margin-left: auto;
  border: 1px solid #d28888;
  color: #9c3434;
  background: #fff;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font-weight: 600;
  white-space: nowrap;
}
.stop-button:disabled {
  opacity: 0.5;
  cursor: wait;
}
.elapsed {
  font-size: 12px;
  white-space: nowrap;
}
.notice {
  margin-top: 8px;
  font-size: 12px;
  color: #536176;
}
.spinner {
  width: 13px;
  height: 13px;
  border: 2px solid #ccd8fb;
  border-top-color: #375bd2;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.error {
  white-space: pre-wrap;
  color: #a72f3c;
  background: #fff0f1;
  padding: 12px;
  border-radius: 6px;
  margin-top: 10px;
  font-size: 13px;
}
.original-comparison {
  display: flex;
  flex: 1;
  min-height: 0;
}
.analysis-results {
  overflow: auto;
  min-height: 0;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 22px;
}
.result-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}
h2 {
  font-size: 19px;
  margin: 0 0 5px;
}
.muted {
  color: #7a8799;
  font-size: 12px;
}
.export-link {
  text-decoration: none;
  font-size: 12px;
  color: #375bd2;
  border: 1px solid #dbe3f5;
  border-radius: 6px;
  padding: 8px 12px;
}
.run-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.run-cards article {
  background: #f8faff;
  border: 1px solid #e4eaf5;
  border-radius: 9px;
  padding: 13px;
  min-width: 0;
}
.run-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  flex-wrap: wrap;
}
.run-title a {
  color: #365bc7;
  text-decoration: none;
  font-weight: 700;
}
.run-title code {
  margin-left: auto;
  color: #7b8697;
  font-size: 11px;
}
.run-badge {
  background: #4c6ef5;
  color: #fff;
  padding: 3px 6px;
  border-radius: 4px;
  font-weight: 700;
}
.run-badge.target {
  background: #20a394;
}
.run-config {
  font-size: 11px;
  color: #718098;
  margin: 8px 0;
  overflow-wrap: anywhere;
}
.run-stats {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
  color: #66758b;
}
.run-stats strong {
  color: #24354c;
  margin-left: 5px;
}
details {
  font-size: 11px;
  color: #78869b;
  margin-top: 10px;
}
details code {
  display: block;
  overflow-wrap: anywhere;
  margin-top: 8px;
}
.warnings,
.topdown-diagnostics {
  background: #fff8e8;
  color: #89651d;
  font-size: 12px;
  padding: 10px 12px;
  border: 1px solid #f0dfb6;
  border-radius: 7px;
  margin-top: 12px;
  overflow-wrap: anywhere;
}
.warnings div + div {
  margin-top: 4px;
}
.query-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1.6fr) minmax(200px, 1fr);
  gap: 20px;
  margin: 20px 0;
}
.field-label {
  font-size: 12px;
  font-weight: 700;
  display: block;
  margin-bottom: 8px;
}
.field-label .muted {
  font-weight: 400;
  margin-left: 6px;
}
.group-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 9px 0;
  max-height: 72px;
  overflow: auto;
}
.group-chips button,
.selected-metrics button {
  border: 1px solid #e2e7f0;
  border-radius: 5px;
  font-size: 10px;
  background: #fff;
  color: #63738c;
  padding: 4px 7px;
  cursor: pointer;
}
.group-chips button.active {
  background: #eaf0ff;
  color: #365bc7;
  border-color: #b8cafa;
}
.metric-options {
  height: 140px;
  overflow: auto;
  border: 1px solid #e2e7f0;
  border-radius: 7px;
  padding: 7px 10px;
}
.metric-options label {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 0;
  font-size: 11px;
}
.metric-options span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric-options small {
  margin-left: auto;
  color: #8694a8;
  font-size: 9px;
}
.metric-count {
  font-size: 10px;
  margin: 5px 0;
}
.selected-metrics {
  max-height: 96px;
  overflow-y: auto;
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.selected-metrics button {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: #f0f4ff;
  color: #375bd2;
}
.query-controls {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sort-fields {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: 10px;
}
.query-button {
  width: 100%;
}
.query-controls > .muted {
  font-size: 10px;
  line-height: 1.6;
}
.pending-note {
  font-size: 12px;
  background: #f0f4ff;
  color: #4965ac;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 12px;
}
.chart-card {
  border: 1px solid #e5eaf3;
  border-radius: 9px;
  padding: 14px;
  margin-bottom: 20px;
}
.chart-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  flex-wrap: wrap;
}
.chart-heading select {
  width: auto;
  max-width: 55%;
  font-size: 11px;
}
.chart-container {
  height: 360px;
  position: relative;
  margin-top: 8px;
}
.table-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  margin-bottom: 9px;
  flex-wrap: wrap;
}
.table-page {
  margin-left: auto;
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 10px;
}
.table-page button {
  border: 1px solid #e3e8ef;
  background: #fff;
  border-radius: 4px;
  padding: 4px;
  cursor: pointer;
}
.table-page button:disabled {
  opacity: 0.4;
}
.table-scroll {
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
th,
td {
  text-align: right;
  padding: 9px 8px;
  border-bottom: 1px solid #edf0f6;
  white-space: nowrap;
}
th {
  background: #f7f9fd;
  color: #72819a;
  font-size: 10px;
  position: sticky;
  top: 0;
}
th:first-child,
td:first-child,
th:nth-child(2),
td:nth-child(2) {
  text-align: left;
}
.metric-name {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}
tr:hover td {
  background: #f8faff;
}
.positive {
  color: #365bc7;
  background: #f0f4ff;
}
.negative {
  color: #a46332;
  background: #fff7ef;
}
.empty-result {
  text-align: center;
  padding: 25px;
  color: #7a8799;
}
@media (max-width: 1100px) {
  .launch-fields {
    grid-template-columns: 1fr 1fr;
  }
  .launch-fields > .primary {
    grid-column: 1/-1;
  }
  .query-grid {
    grid-template-columns: 1fr;
  }
  .run-cards {
    grid-template-columns: 1fr;
  }
  .run-stats {
    flex-wrap: wrap;
  }
}
@media (max-width: 700px) {
  .launch-fields,
  .archive-choices {
    grid-template-columns: 1fr;
  }
  .analysis-results {
    overflow: visible;
    padding: 14px;
  }
  .developer-comparison {
    height: auto;
  }
  .original-comparison {
    min-height: 500px;
  }
  .run-title code {
    margin-left: 0;
  }
}
</style>
