<template>
  <div id="app-root">
    <header class="topbar">
      <div class="brand">
        <img class="brand-mark" src="./assets/logo.svg" alt="香山" />
        <div>
          <h1>XiangShan Dashboard</h1>
          <div class="muted">内部性能分析 · SPEC2006 GCC16</div>
        </div>
      </div>
      <nav class="hero-tabs" aria-label="看板视图">
        <button
          class="tab-btn"
          :class="{ active: view === 'trend' }"
          @click="
            view = 'trend';
            chartMode = 'score';
          "
        >
          性能趋势
        </button>
        <button
          class="tab-btn"
          :class="{ active: view === 'analysis' }"
          @click="view = 'analysis'"
        >
          性能分析
        </button>
      </nav>
      <div class="header-links">
        <span class="badge">内部部署</span>
        <a
          href="/xiangshan-performance-dashboard/reports/20261103/"
          target="_blank"
          rel="noopener"
          >分析报告 ↗</a
        >
        <a
          href="/xiangshan-performance-dashboard/reports/tea-dip/"
          target="_blank"
          rel="noopener"
          >TEA+DIP ↗</a
        >
      </div>
    </header>

    <main class="content">
      <aside class="panel-sidebar">
        <section class="panel-card">
          <h2 class="section-title">数据范围</h2>
          <label for="dataset">切片集</label>
          <select
            id="dataset"
            class="control-input"
            v-model="datasetId"
            @change="selectDataset"
          >
            <option
              v-for="entry in catalog?.datasets"
              :key="entry.id"
              :value="entry.id"
            >
              {{ entry.label }} · {{ entry.run_count }} 次测量
            </option>
          </select>
          <label for="coverage">覆盖范围</label>
          <select
            id="coverage"
            class="control-input"
            v-model="coverage"
            :disabled="loading"
          >
            <option value="">全部范围</option>
            <option v-for="scope in coverages" :key="scope" :value="scope">
              {{ scope }}
            </option>
          </select>
          <p class="muted small">
            {{ filteredRuns.length }} 次测量 · {{ commitCount }} 个提交
          </p>
        </section>

        <section class="panel-card" v-if="history?.runs.length">
          <h2 class="section-title">A/B 测量</h2>
          <label for="base">基线 A</label>
          <select
            id="base"
            class="control-input"
            v-model="baseId"
            :disabled="loading"
          >
            <option
              v-for="run in filteredRuns"
              :key="run.run_id"
              :value="run.run_id"
            >
              {{ observationLabel(run) }}
            </option>
          </select>
          <label for="target">目标 B</label>
          <select
            id="target"
            class="control-input"
            v-model="targetId"
            :disabled="loading"
          >
            <option
              v-for="run in filteredRuns"
              :key="run.run_id"
              :value="run.run_id"
            >
              {{ observationLabel(run) }}
            </option>
          </select>
          <button
            class="sidebar-btn wide"
            @click="swapRuns"
            :disabled="loading"
          >
            交换 A / B
          </button>
        </section>

        <section
          class="panel-card benchmark-card"
          v-if="view === 'trend' && scoreNames.length"
        >
          <h2 class="section-title">评分项目</h2>
          <div class="benchmark-list">
            <label class="checkbox-row" v-for="name in scoreNames" :key="name">
              <input type="checkbox" v-model="selectedScores" :value="name" />{{
                name
              }}
            </label>
          </div>
        </section>

        <section
          class="panel-card"
          v-if="view === 'analysis' && history?.runs.length"
        >
          <h2 class="section-title">分析对象</h2>
          <label for="workload">Workload</label>
          <select id="workload" class="control-input" v-model="workload">
            <option v-for="name in history.workloads" :key="name" :value="name">
              {{ name }}
            </option>
          </select>
          <label for="slice">切片</label>
          <select id="slice" class="control-input" v-model="sliceId">
            <option
              v-for="slice in workloadSlices"
              :key="slice.slice_id"
              :value="slice.slice_id"
            >
              {{ slice.checkpoint }} · {{ slice.name }}
            </option>
          </select>
          <label for="counterUnit">计数器口径</label>
          <select id="counterUnit" class="control-input" v-model="counterUnit">
            <option value="raw">原始计数</option>
            <option value="per_kinst">每千指令</option>
          </select>
        </section>

        <section class="panel-card">
          <h2 class="section-title">导出与报告</h2>
          <button
            class="sidebar-btn wide"
            @click="exportChart"
            :disabled="loading || !chartNames.length"
          >
            导出当前趋势 PNG
          </button>
          <button
            class="sidebar-btn wide"
            @click="exportComparison"
            :disabled="loading || !base || !target"
          >
            导出 A/B CSV
          </button>
          <a
            class="sidebar-btn wide"
            :href="legacyUrl"
            target="_blank"
            rel="noopener"
            >完整相关性与历史分析 ↗</a
          >
        </section>
      </aside>

      <div class="workspace" :aria-busy="loading">
        <div v-if="loading" class="panel-card notice" role="status">
          正在加载测量数据…
        </div>
        <div v-if="error" class="panel-card error" role="alert">
          {{ error
          }}<button class="sidebar-btn" @click="bootstrap">重新加载</button>
        </div>
        <div
          v-if="!loading && !error && !history?.runs.length"
          class="panel-card notice"
        >
          当前数据集暂无测量。导入并导出数据后重新加载。
        </div>

        <template v-if="history && base && target && !loading">
          <section class="summary-grid" aria-label="A/B 摘要">
            <article class="panel-card summary-card">
              <span class="muted">{{ summaryScore }} · A → B</span>
              <strong
                >{{ format(scoreA) }} <span class="arrow">→</span>
                {{ format(scoreB) }}</strong
              >
              <span :class="tone(scoreChange)">{{
                formatPercent(scoreChange)
              }}</span>
            </article>
            <article class="panel-card summary-card">
              <span class="muted">共同切片 · 套件 IPC 变化</span>
              <strong :class="tone(commonChange)">{{
                formatPercent(commonChange)
              }}</strong>
              <small class="muted">共同 {{ commonSliceCount }} 个切片</small>
            </article>
            <article class="panel-card summary-card">
              <span class="muted">目标 B · 切片覆盖</span>
              <strong
                >{{ target.valid_slices }}
                <span class="arrow"
                  >/ {{ target.expected_slices }}</span
                ></strong
              >
              <small class="muted">{{ target.score_coverage || "—" }}</small>
            </article>
          </section>

          <section class="panel-surface trend-section">
            <div class="trend-toolbar" v-if="view === 'analysis'">
              <label for="chartMode">趋势对象</label>
              <select id="chartMode" class="control-input" v-model="chartMode">
                <option value="workload">{{ workload }} · Workload IPC</option>
                <option value="slice">当前切片 IPC</option>
                <option value="counter">当前切片计数器</option>
              </select>
              <select
                id="chartMetric"
                class="control-input"
                v-if="chartMode === 'counter'"
                v-model="metricId"
                aria-label="趋势计数器"
              >
                <option
                  v-for="metric in catalog?.metrics"
                  :key="metric.metric_id"
                  :value="metric.metric_id"
                >
                  {{ metric.display_name }}
                </option>
              </select>
            </div>
            <MetricChartPanel
              ref="chartPanel"
              :tab="chartConfig"
              :runs="chartRuns"
              :selected-benchmarks="chartNames"
              :run-data-by-id="chartData"
              :base-run-id="baseId"
              :target-run-id="targetId"
              :loading="loading"
              :no-data-text="loading ? '正在加载测量…' : '当前对象暂无有效数据'"
              :summary="datasetEntry?.label || ''"
              :t="translate"
              @run-select="targetId = $event"
            />
          </section>

          <section class="panel-card" v-if="view === 'trend'">
            <div class="section-heading">
              <h2>评分 A/B 明细</h2>
              <button class="sidebar-btn" @click="view = 'analysis'">
                下钻 Workload 与切片 →
              </button>
            </div>
            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>项目</th>
                    <th>A</th>
                    <th>B</th>
                    <th>变化</th>
                    <th>评分覆盖 A / B</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in scoreRows" :key="row.name">
                    <td>{{ row.name }}</td>
                    <td>{{ format(row.a?.value) }}</td>
                    <td>{{ format(row.b?.value) }}</td>
                    <td :class="tone(row.change)">
                      {{ formatPercent(row.change) }}
                    </td>
                    <td>
                      {{ formatCoverage(row.a?.minimum_coverage) }} /
                      {{ formatCoverage(row.b?.minimum_coverage) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <template v-if="view === 'analysis'">
            <section class="panel-card" id="workloads">
              <div class="section-heading">
                <h2>Workload A/B</h2>
                <div class="inline-controls">
                  <input
                    id="workloadSearch"
                    class="control-input"
                    v-model="workloadSearch"
                    placeholder="搜索 workload"
                    aria-label="搜索 workload"
                  />
                  <select
                    id="workloadFilter"
                    class="control-input"
                    v-model="workloadFilter"
                    aria-label="性能变化筛选"
                  >
                    <option value="all">全部变化</option>
                    <option value="regression">退化 &gt; 0.5%</option>
                    <option value="improvement">提升 &gt; 0.5%</option>
                  </select>
                </div>
              </div>
              <div
                class="visual-tabs"
                role="group"
                aria-label="Workload 可视化"
              >
                <button
                  class="tab-btn"
                  :class="{ active: workloadView === 'bars' }"
                  @click="workloadView = 'bars'"
                >
                  柱状图
                </button>
                <button
                  class="tab-btn"
                  :class="{ active: workloadView === 'heatmap' }"
                  @click="workloadView = 'heatmap'"
                >
                  历史热图
                </button>
                <button
                  class="tab-btn"
                  :class="{ active: workloadView === 'table' }"
                  @click="workloadView = 'table'"
                >
                  数值明细
                </button>
              </div>
              <WorkloadChanges
                v-if="workloadView === 'bars'"
                :rows="visibleWorkloads"
                :selected="workload"
                @select="chooseWorkload"
              />
              <div
                class="table-scroll workload-heatmap"
                v-else-if="workloadView === 'heatmap'"
              >
                <table id="workloadHeatmap">
                  <thead>
                    <tr>
                      <th>Workload</th>
                      <th
                        v-for="run in filteredRuns"
                        :key="run.run_id"
                        :title="run.subject"
                      >
                        {{ run.short_sha }}<b v-if="run.run_id === baseId"> A</b
                        ><b v-if="run.run_id === targetId"> B</b>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in heatmapRows" :key="row.name">
                      <th>
                        <button
                          class="text-button"
                          @click="chooseWorkload(row.name)"
                        >
                          {{ row.name }}
                        </button>
                      </th>
                      <td v-for="cell in row.cells" :key="cell.runId">
                        <button
                          type="button"
                          class="heat-cell"
                          :class="{
                            current:
                              workload === row.name && targetId === cell.runId,
                          }"
                          :data-workload="row.name"
                          :data-run="cell.runId"
                          :style="heatStyle(cell.change)"
                          :title="`共同 ${cell.count} 个切片`"
                          @click="
                            targetId = cell.runId;
                            chooseWorkload(row.name);
                          "
                        >
                          {{ formatPercent(cell.change) }}
                        </button>
                      </td>
                    </tr>
                    <tr v-if="!heatmapRows.length">
                      <td :colspan="filteredRuns.length + 1">
                        暂无符合筛选的 workload
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="table-scroll workload-table" v-else>
                <table>
                  <thead>
                    <tr>
                      <th>Workload</th>
                      <th>IPC A</th>
                      <th>IPC B</th>
                      <th>IPC 变化</th>
                      <th>共同切片</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in visibleWorkloads"
                      :key="row.name"
                      :class="{ selected: workload === row.name }"
                      @click="chooseWorkload(row.name)"
                    >
                      <td>
                        <button
                          class="text-button"
                          @click.stop="chooseWorkload(row.name)"
                        >
                          {{ row.name }}
                        </button>
                      </td>
                      <td>{{ format(row.pair.a) }}</td>
                      <td>{{ format(row.pair.b) }}</td>
                      <td :class="tone(row.change)">
                        {{ formatPercent(row.change) }}
                      </td>
                      <td>{{ row.pair.count }}</td>
                    </tr>
                    <tr v-if="!visibleWorkloads.length">
                      <td colspan="5">暂无符合筛选的 workload</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section class="panel-card" id="slices">
              <div class="section-heading">
                <h2>{{ workload }} · 切片 A/B</h2>
                <span class="muted"
                  >共同 {{ currentWorkloadPair?.count || 0 }} 个切片</span
                >
              </div>
              <div class="table-scroll slice-table">
                <table>
                  <thead>
                    <tr>
                      <th>Checkpoint</th>
                      <th>权重 A / B</th>
                      <th>IPC A</th>
                      <th>IPC B</th>
                      <th>IPC 变化</th>
                      <th>加权 ΔCPI</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in sliceRows"
                      :key="row.slice.slice_id"
                      :data-slice="row.slice.slice_id"
                      :class="{ selected: sliceId === row.slice.slice_id }"
                      @click="chooseSlice(row.slice.slice_id)"
                    >
                      <td>
                        <button
                          class="text-button"
                          @click.stop="chooseSlice(row.slice.slice_id)"
                        >
                          {{ row.slice.checkpoint }}
                        </button>
                      </td>
                      <td>
                        {{ format(row.left?.weight, 6) }} /
                        {{ format(row.right?.weight, 6) }}
                      </td>
                      <td>{{ format(row.left?.ipc) }}</td>
                      <td>{{ format(row.right?.ipc) }}</td>
                      <td :class="tone(row.change)">
                        {{ formatPercent(row.change) }}
                      </td>
                      <td>{{ format(row.impact, 8) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section class="panel-card" id="sliceCounters" v-if="selectedSlice">
              <div class="section-heading">
                <div>
                  <h2>
                    {{ selectedSlice.workload }} /
                    {{ selectedSlice.checkpoint }} · 性能计数器
                  </h2>
                  <p class="muted small">
                    {{ base.short_sha }} → {{ target.short_sha }} ·
                    {{ counterUnit === "raw" ? "原始计数" : "每千指令" }}
                  </p>
                </div>
                <div class="inline-controls">
                  <a
                    class="sidebar-btn"
                    :href="teaDipUrl"
                    target="_blank"
                    rel="noopener"
                    >TEA+DIP 下钻 ↗</a
                  >
                  <label class="sr-only" for="sliceCounterUnit"
                    >明细计数器口径</label
                  ><select
                    id="sliceCounterUnit"
                    class="control-input"
                    v-model="counterUnit"
                  >
                    <option value="raw">原始计数</option>
                    <option value="per_kinst">每千指令</option>
                  </select>
                </div>
              </div>
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>指标 / 单位</th>
                      <th>A</th>
                      <th>B</th>
                      <th>B − A</th>
                      <th>变化</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in counterRows"
                      :key="row.metric.metric_id"
                      :data-metric="row.metric.metric_id"
                      :class="{
                        selected:
                          chartMode === 'counter' &&
                          metricId === row.metric.metric_id,
                      }"
                    >
                      <td>
                        <button
                          class="text-button"
                          @click="
                            metricId = row.metric.metric_id;
                            chartMode = 'counter';
                          "
                        >
                          {{ row.metric.display_name }}</button
                        ><small class="muted metric-unit">{{
                          metricUnit(row.metric)
                        }}</small>
                      </td>
                      <td>{{ formatCounter(row.a) }}</td>
                      <td>{{ formatCounter(row.b) }}</td>
                      <td>{{ formatCounter(row.delta, true) }}</td>
                      <td>{{ formatPercent(row.change) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </template>

          <details class="panel-card provenance">
            <summary>测量来源</summary>
            <dl>
              <dt>A · run ID</dt>
              <dd>{{ base.run_id }}</dd>
              <dt>B · run ID</dt>
              <dd>{{ target.run_id }}</dd>
              <dt>A · 原始来源</dt>
              <dd>{{ base.source_uri }}</dd>
              <dt>B · 原始来源</dt>
              <dd>{{ target.source_uri }}</dd>
              <dt>数据更新时间</dt>
              <dd>{{ catalog?.generated_at }}</dd>
            </dl>
          </details>
        </template>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, shallowRef, watch } from "vue";
import MetricChartPanel from "./components/panels/MetricChartPanel.vue";
import WorkloadChanges from "./components/panels/WorkloadChanges.vue";
import comparison from "@comparison";
import {
  BASE_METRIC,
  finite,
  History,
  loadJson,
  observationLabel,
  percent,
} from "./services/history";
import type { ChartConfig } from "./config/tabs";
import type { Catalog, CounterUnit, Dataset, Metric } from "./types/history";
import type { NormalizedRun, ReportPayload } from "./types/data";

const params = new URLSearchParams(location.search);
const dataRoot = params.get("data") || "/outputs/mainline-history";
const catalog = shallowRef<Catalog | null>(null);
const history = shallowRef<History | null>(null);
const datasetId = ref("");
const view = ref<"trend" | "analysis">("trend");
const chartMode = ref<"score" | "workload" | "slice" | "counter">("score");
const loading = ref(false),
  error = ref("");
const baseId = ref(""),
  targetId = ref(""),
  coverage = ref("");
const workload = ref(""),
  sliceId = ref(""),
  metricId = ref("");
const counterUnit = ref<CounterUnit>("raw");
const selectedScores = ref<string[]>([]);
const workloadSearch = ref(""),
  workloadFilter = ref("all");
const workloadView = ref<"bars" | "heatmap" | "table">("bars");
const chartPanel = ref<InstanceType<typeof MetricChartPanel> | null>(null);
let generation = 0;

const datasetEntry = computed(() =>
  catalog.value?.datasets.find((row) => row.id === datasetId.value),
);
const filteredRuns = computed(() =>
  (history.value?.runs || []).filter(
    (run) => !coverage.value || run.score_coverage === coverage.value,
  ),
);
const coverages = computed(() =>
  [
    ...new Set(
      history.value?.runs
        .map((run) => run.score_coverage)
        .filter((value): value is string => !!value),
    ),
  ].sort(),
);
const commitCount = computed(
  () => new Set(filteredRuns.value.map((run) => run.commit_sha)).size,
);
const base = computed(() =>
  history.value?.runs.find((run) => run.run_id === baseId.value),
);
const target = computed(() =>
  history.value?.runs.find((run) => run.run_id === targetId.value),
);
const selectedSlice = computed(() =>
  history.value?.slices.find((slice) => slice.slice_id === sliceId.value),
);
const selectedMetric = computed(() =>
  catalog.value?.metrics.find((metric) => metric.metric_id === metricId.value),
);
const workloadSlices = computed(() =>
  history.value && base.value && target.value
    ? history.value.pairSlices(base.value, target.value, workload.value)
    : [],
);
const scoreNames = computed(() =>
  [
    ...new Set(
      history.value?.runs.flatMap((run) =>
        run.scores.map((row) => row.object_id),
      ),
    ),
  ].sort(
    (a, b) =>
      Number(b.startsWith("SPEC")) - Number(a.startsWith("SPEC")) ||
      a.localeCompare(b),
  ),
);
const summaryScore = computed(
  () => selectedScores.value[0] || datasetEntry.value?.suite || "SPEC2006",
);
const scoreA = computed(() =>
  base.value
    ? (history.value?.score(base.value, summaryScore.value)?.value ?? null)
    : null,
);
const scoreB = computed(() =>
  target.value
    ? (history.value?.score(target.value, summaryScore.value)?.value ?? null)
    : null,
);
const scoreAssessment = computed(() =>
  history.value && base.value && target.value
    ? history.value.scorePair(base.value, target.value, summaryScore.value)
    : null,
);
const scoreChange = computed(() =>
  scoreAssessment.value?.allowed ? percent(scoreA.value, scoreB.value) : null,
);
const scoreRows = computed(() =>
  history.value && base.value && target.value
    ? scoreNames.value
        .filter((name) => selectedScores.value.includes(name))
        .map((name) => {
          const h = history.value!,
            a = h.score(base.value!, name),
            b = h.score(target.value!, name),
            pair = h.scorePair(base.value!, target.value!, name);
          return {
            name,
            a,
            b,
            pair,
            change: pair.allowed
              ? percent(a?.value ?? null, b?.value ?? null)
              : null,
          };
        })
    : [],
);
const workloadRows = computed(() =>
  history.value && base.value && target.value
    ? history.value.workloads
        .map((name) => {
          const pair = history.value!.workloadPair(
            base.value!,
            target.value!,
            name,
          );
          return {
            name,
            pair,
            change: pair.allowed ? percent(pair.a, pair.b) : null,
          };
        })
        .sort(
          (a, b) =>
            Math.abs(b.change ?? 0) - Math.abs(a.change ?? 0) ||
            a.name.localeCompare(b.name),
        )
    : [],
);
const visibleWorkloads = computed(() =>
  workloadRows.value.filter(
    (row) =>
      row.name.toLowerCase().includes(workloadSearch.value.toLowerCase()) &&
      (workloadFilter.value === "all" ||
        (finite(row.change) &&
          (workloadFilter.value === "regression"
            ? row.change < -0.5
            : row.change > 0.5))),
  ),
);
const heatmapRows = computed(() =>
  history.value && base.value
    ? visibleWorkloads.value.map((row) => ({
        name: row.name,
        cells: filteredRuns.value.map((run) => {
          const pair = history.value!.workloadPair(base.value!, run, row.name);
          return {
            runId: run.run_id,
            count: pair.count,
            change: pair.allowed ? percent(pair.a, pair.b) : null,
          };
        }),
      }))
    : [],
);
const comparableWorkloads = computed(() =>
  workloadRows.value.filter((row) => finite(row.change)),
);
const commonChange = computed(() =>
  comparableWorkloads.value.length
    ? (Math.exp(
        comparableWorkloads.value.reduce(
          (sum, row) => sum + Math.log(row.pair.b! / row.pair.a!),
          0,
        ) / comparableWorkloads.value.length,
      ) -
        1) *
      100
    : null,
);
const commonSliceCount = computed(() =>
  comparableWorkloads.value.reduce((sum, row) => sum + row.pair.count, 0),
);
const currentWorkloadPair = computed(
  () => workloadRows.value.find((row) => row.name === workload.value)?.pair,
);
const sliceRows = computed(() =>
  history.value && base.value && target.value
    ? workloadSlices.value.map((slice) => {
        const h = history.value!,
          left = h.result(base.value!, slice),
          right = h.result(target.value!, slice),
          pair = h.slicePair(base.value!, target.value!, slice);
        const impact =
          pair.allowed && finite(left?.weight) && left.weight === right?.weight
            ? left.weight * (1 / right.ipc! - 1 / left.ipc!)
            : null;
        return {
          slice,
          left,
          right,
          pair,
          impact,
          change: pair.allowed
            ? percent(left?.ipc ?? null, right?.ipc ?? null)
            : null,
        };
      })
    : [],
);
const counterRows = computed(() =>
  history.value && base.value && target.value && selectedSlice.value
    ? catalog.value!.metrics.map((metric) => {
        const h = history.value!,
          slice = selectedSlice.value!;
        const a = h.counterValue(base.value!, slice, metric, counterUnit.value),
          b = h.counterValue(target.value!, slice, metric, counterUnit.value);
        const pair = h.counterPair(
          base.value!,
          target.value!,
          slice,
          metric,
          counterUnit.value,
        );
        return {
          metric,
          a,
          b,
          pair,
          delta: pair.allowed && finite(a) && finite(b) ? b - a : null,
          change: pair.allowed ? percent(a, b) : null,
        };
      })
    : [],
);

const chartNames = computed(() =>
  chartMode.value === "score"
    ? selectedScores.value
    : chartMode.value === "counter"
      ? selectedMetric.value
        ? [selectedMetric.value.display_name]
        : []
      : chartMode.value === "workload"
        ? [workload.value]
        : selectedSlice.value
          ? [
              `${selectedSlice.value.workload} / ${selectedSlice.value.checkpoint}`,
            ]
          : [],
);
const chartConfig = computed<ChartConfig>(() => ({
  id: chartMode.value,
  titleKey:
    chartMode.value === "score"
      ? "性能趋势"
      : chartMode.value === "counter"
        ? "性能计数器趋势"
        : "IPC 趋势",
  metricKey:
    chartMode.value === "score"
      ? "score"
      : chartMode.value === "counter"
        ? "counter"
        : "ipc",
  metricLabel:
    chartMode.value === "score"
      ? "Score"
      : chartMode.value === "counter" && selectedMetric.value
        ? metricUnit(selectedMetric.value)
        : "IPC",
  axisMode: "date",
}));
const chartRuns = computed<NormalizedRun[]>(() =>
  filteredRuns.value.map((run) => ({
    runId: run.run_id,
    hash: run.commit_sha,
    title: run.subject,
    dateMs: run.commit_epoch * 1000,
    coverage: run.score_coverage,
  })),
);
const chartData = computed<Record<string, ReportPayload>>(() => {
  const h = history.value;
  if (!h) return {};
  return Object.fromEntries(
    filteredRuns.value.map((run) => [
      run.run_id,
      Object.fromEntries(
        chartNames.value.map((name) => {
          const value =
            chartMode.value === "score"
              ? (h.score(run, name)?.value ?? null)
              : chartMode.value === "counter" &&
                  selectedSlice.value &&
                  selectedMetric.value
                ? h.counterValue(
                    run,
                    selectedSlice.value,
                    selectedMetric.value,
                    counterUnit.value,
                  )
                : chartMode.value === "slice" && selectedSlice.value
                  ? (h.result(run, selectedSlice.value)?.ipc ?? null)
                  : chartMode.value === "workload"
                    ? (run.workloads.find(
                        (row) =>
                          row.workload === workload.value &&
                          row.status === "valid",
                      )?.weighted_ipc ?? null)
                    : null;
          return [name, { [chartConfig.value.metricKey]: value }];
        }),
      ),
    ]),
  );
});

const legacyUrl = computed(
  () =>
    `/xiangshan-performance-dashboard/?${new URLSearchParams({ data: dataRoot, dataset: datasetId.value })}`,
);
const teaDipUrl = computed(() => {
  if (!selectedSlice.value || !target.value) return "";
  const slice = selectedSlice.value,
    run = target.value;
  return `/xiangshan-performance-dashboard/reports/tea-dip/index.html?${new URLSearchParams({ slice: `${slice.workload}:${slice.checkpoint}`, checkpoint_root: slice.checkpoint_identity.replace(/\/checkpoint\/?$/, ""), source_uri: run.source_uri, observation: `${run.short_sha} · 测量 ${run.run_id.slice(0, 12)}` })}`;
});

function format(value: unknown, digits = 3): string {
  return finite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits })
    : "—";
}
function formatCounter(value: unknown, signed = false): string {
  return finite(value)
    ? `${signed && value > 0 ? "+" : ""}${format(value, counterUnit.value === "per_kinst" ? 6 : 2)}`
    : "—";
}
function formatPercent(value: unknown): string {
  return finite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}%` : "—";
}
function formatCoverage(value: unknown): string {
  return finite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}
function tone(value: unknown): string {
  return !finite(value) || Math.abs(value) <= 0.5
    ? "neutral"
    : value > 0
      ? "good"
      : "bad";
}
function metricUnit(metric: Metric): string {
  return counterUnit.value === "per_kinst" && metric.metric_id !== BASE_METRIC
    ? `${metric.unit}/KInst`
    : metric.unit;
}
const translate = (key: string) =>
  ({
    dateAxis: "提交日期",
    runIdAxis: "测量",
    commit: "提交",
    commitTitle: "标题",
    date: "日期",
    coverage: "覆盖范围",
    exportNoData: "暂无可导出的趋势",
  })[key] || key;

function heatStyle(value: number | null) {
  if (value === null) return { backgroundColor: "#f4f7fb" };
  const alpha = 0.04 + Math.min(Math.abs(value) / 10, 1) * 0.3;
  return {
    backgroundColor: `rgb(${value >= 0 ? "25 132 93" : "194 75 64"} / ${alpha})`,
  };
}

function resetPair() {
  const runs = filteredRuns.value,
    h = history.value;
  if (!h || !runs.length) {
    baseId.value = targetId.value = "";
    return;
  }
  const b =
    [...runs].reverse().find((run) => run.valid_slices > 0) ||
    runs[runs.length - 1];
  const earlier = runs.slice(0, runs.indexOf(b));
  const a =
    [...earlier]
      .reverse()
      .find(
        (run) =>
          run.commit_sha !== b.commit_sha &&
          comparison.runs(run, b).allowed &&
          h
            .pairSlices(run, b)
            .some((slice) => h.slicePair(run, b, slice).allowed),
      ) ||
    earlier[earlier.length - 1] ||
    b;
  baseId.value = a.run_id;
  targetId.value = b.run_id;
}
function swapRuns() {
  [baseId.value, targetId.value] = [targetId.value, baseId.value];
}
function chooseWorkload(name: string) {
  workload.value = name;
  chartMode.value = "workload";
}
async function chooseSlice(id: string) {
  sliceId.value = id;
  chartMode.value = "slice";
  await nextTick();
  document
    .getElementById("sliceCounters")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}
watch(coverage, resetPair);
watch(workloadSlices, (slices) => {
  if (!slices.some((slice) => slice.slice_id === sliceId.value))
    sliceId.value =
      slices.find(
        (slice) =>
          history.value!.slicePair(base.value!, target.value!, slice).allowed,
      )?.slice_id ||
      slices[0]?.slice_id ||
      "";
});
watch(view, (value) => {
  chartMode.value = value === "trend" ? "score" : "workload";
});

async function selectDataset() {
  const request = ++generation;
  loading.value = true;
  error.value = "";
  history.value = null;
  try {
    const payload = await loadJson<Dataset>(
      dataRoot,
      `${datasetId.value}.json`,
    );
    if (request !== generation) return;
    const h = new History(catalog.value!, payload);
    history.value = h;
    coverage.value = "";
    workload.value = h.workloads.includes("mcf") ? "mcf" : h.workloads[0] || "";
    metricId.value =
      catalog.value!.metrics.find(
        (metric) => metric.metric_id === "branch_mispredictions",
      )?.metric_id ||
      catalog.value!.metrics[0]?.metric_id ||
      "";
    selectedScores.value = scoreNames.value.includes(datasetEntry.value!.suite)
      ? [datasetEntry.value!.suite]
      : scoreNames.value.slice(0, 1);
    resetPair();
    const url = new URL(location.href);
    url.searchParams.set("dataset", datasetId.value);
    window.history.replaceState(null, "", url);
  } catch (reason) {
    if (request === generation) error.value = String(reason);
  } finally {
    if (request === generation) loading.value = false;
  }
}
async function bootstrap() {
  loading.value = true;
  error.value = "";
  try {
    const data = await loadJson<Catalog>(dataRoot, "manifest.json");
    if (data.schema_version !== "mainline-history-dashboard/v1")
      throw new Error("不支持的看板数据格式");
    catalog.value = data;
    datasetId.value =
      data.datasets.find((entry) => entry.id === params.get("dataset"))?.id ||
      data.default_dataset;
    if (data.datasets.length) await selectDataset();
    else loading.value = false;
  } catch (reason) {
    error.value = `数据加载失败：${reason}`;
    loading.value = false;
  }
}
function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
async function exportChart() {
  try {
    const image = await chartPanel.value!.exportPng();
    const response = await fetch(image);
    download(await response.blob(), `performance-${chartMode.value}.png`);
  } catch (reason) {
    error.value = `图表导出失败：${reason}`;
  }
}
function exportComparison() {
  const a = base.value!,
    b = target.value!;
  const identity = [a.run_id, a.commit_sha, b.run_id, b.commit_sha];
  const header = [
    "run_a",
    "commit_a",
    "run_b",
    "commit_b",
    "level",
    "object",
    "metric",
    "unit",
    "value_a",
    "value_b",
    "delta",
    "change_pct",
    "comparable",
    "reasons",
  ];
  const rows: unknown[][] =
    view.value === "trend"
      ? scoreRows.value.map((row) => [
          ...identity,
          "score",
          row.name,
          "score",
          "score",
          row.a?.value,
          row.b?.value,
          row.pair.allowed ? row.b!.value! - row.a!.value! : null,
          row.change,
          row.pair.allowed,
          [...row.pair.reasons, ...row.pair.notes].join("; "),
        ])
      : [
          ...workloadRows.value.map((row) => [
            ...identity,
            "workload",
            row.name,
            "ipc",
            "IPC",
            row.pair.a,
            row.pair.b,
            row.pair.allowed ? row.pair.b! - row.pair.a! : null,
            row.change,
            row.pair.allowed,
            [...row.pair.reasons, ...row.pair.notes].join("; "),
          ]),
          ...sliceRows.value.map((row) => [
            ...identity,
            "slice",
            row.slice.slice_id,
            "ipc",
            "IPC",
            row.left?.ipc,
            row.right?.ipc,
            row.pair.allowed ? row.right!.ipc! - row.left!.ipc! : null,
            row.change,
            row.pair.allowed,
            [...row.pair.reasons, ...row.pair.notes].join("; "),
          ]),
          ...counterRows.value.map((row) => [
            ...identity,
            "counter",
            selectedSlice.value!.slice_id,
            row.metric.metric_id,
            metricUnit(row.metric),
            row.a,
            row.b,
            row.delta,
            row.change,
            row.pair.allowed,
            [...row.pair.reasons, ...row.pair.notes].join("; "),
          ]),
        ];
  const csv = [header, ...rows]
    .map((row) =>
      row
        .map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`)
        .join(","),
    )
    .join("\r\n");
  download(
    new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }),
    `performance-${a.short_sha}-to-${b.short_sha}.csv`,
  );
}
onMounted(bootstrap);
</script>
