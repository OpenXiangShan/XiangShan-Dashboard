<template>
  <div ref="exportRoot" class="panel-surface panel-shell chart-panel">
    <div class="panel-header">
      <h2 class="panel-title">
        {{ title }}
        <span v-if="summary" class="panel-subtitle">{{ summary }}</span>
      </h2>
    </div>
    <div class="chart-wrap" v-if="hasData">
      <canvas ref="canvasRef"></canvas>
      <div v-if="loading" class="loading-overlay">
        {{ noDataText }}
      </div>
    </div>
    <div class="empty" v-else>
      {{ noDataText }}
    </div>
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
import type { Chart } from "chart.js";
import { toPng } from "html-to-image";
import type { ChartConfig } from "../../config/tabs";
import type { SpecVersion } from "../../config/spec";
import type { NormalizedRun, ReportPayload } from "../../types/data";
import { renderMetricChart } from "../../composables/useMetricChart";
import {
  EXPORT_LAYOUT_HEIGHT,
  EXPORT_LAYOUT_WIDTH,
  EXPORT_PIXEL_RATIO,
} from "../../constants/export";

const props = defineProps<{
  tab: ChartConfig;
  runs: NormalizedRun[];
  selectedBenchmarks: string[];
  runDataByHash: Record<string, ReportPayload>;
  loading: boolean;
  noDataText: string;
  geomeanMissing: Record<number, Record<string, string[]>>;
  specVersion: SpecVersion;
  t: (key: string) => string;
  title: string;
  summary: string;
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const exportRoot = ref<HTMLElement | null>(null);
let chart: Chart | null = null;
let renderGeneration = 0;

const hasData = computed(
  () => props.runs.length > 0 && props.selectedBenchmarks.length > 0,
);

async function redraw() {
  const currentGeneration = ++renderGeneration;

  if (!hasData.value) {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    return;
  }

  await nextTick();
  if (currentGeneration !== renderGeneration) return;
  if (!hasData.value || !canvasRef.value) return;

  chart = renderMetricChart({
    canvas: canvasRef.value,
    chart,
    tab: props.tab,
    runs: props.runs,
    selectedBenchmarks: props.selectedBenchmarks,
    runDataByHash: props.runDataByHash,
    geomeanMissing: props.geomeanMissing,
    specVersion: props.specVersion,
    t: props.t,
  });
}

onMounted(redraw);
watch(
  () => [
    props.tab.id,
    props.runs,
    props.selectedBenchmarks,
    props.runDataByHash,
    props.geomeanMissing,
    props.specVersion,
  ],
  redraw,
  { deep: true },
);

onBeforeUnmount(() => {
  renderGeneration += 1;
  if (chart) chart.destroy();
  chart = null;
});

async function exportPng(): Promise<string> {
  const source = exportRoot.value;
  if (!source || !hasData.value) {
    throw new Error(props.t("exportNoData"));
  }

  const clone = source.cloneNode(true) as HTMLElement;
  const renderHost = document.createElement("div");
  renderHost.style.position = "fixed";
  renderHost.style.left = "-100000px";
  renderHost.style.top = "0";
  renderHost.style.width = `${EXPORT_LAYOUT_WIDTH}px`;
  renderHost.style.height = `${EXPORT_LAYOUT_HEIGHT}px`;
  renderHost.style.overflow = "visible";

  clone.style.position = "static";
  clone.style.width = `${EXPORT_LAYOUT_WIDTH}px`;
  clone.style.height = `${EXPORT_LAYOUT_HEIGHT}px`;
  clone.style.minHeight = "0";
  clone.style.backgroundColor = "#ffffff";
  clone.style.borderRadius = "var(--radius-card)";
  clone.style.overflow = "hidden";

  const sourceCanvas = source.querySelector("canvas");
  const cloneCanvas = clone.querySelector("canvas");
  if (sourceCanvas && cloneCanvas) {
    const image = document.createElement("img");
    image.src = sourceCanvas.toDataURL("image/png");
    image.alt = "";
    image.style.display = "block";
    image.style.width = "100%";
    image.style.height = "100%";
    cloneCanvas.replaceWith(image);
  }

  renderHost.appendChild(clone);
  document.body.appendChild(renderHost);
  try {
    return await toPng(clone, {
      width: EXPORT_LAYOUT_WIDTH,
      height: EXPORT_LAYOUT_HEIGHT,
      pixelRatio: EXPORT_PIXEL_RATIO,
      cacheBust: true,
    });
  } finally {
    renderHost.remove();
  }
}

defineExpose({ exportPng });
</script>

<style scoped>
.chart-panel {
  min-height: 380px;
  height: 100%;
}

.chart-wrap {
  position: relative;
  height: auto;
  width: 100%;
  flex: 1;
  min-height: 0;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #5b6070;
  font-weight: 600;
  text-align: center;
  word-break: break-all;
  padding: 0 16px;
  background: rgb(255 255 255 / 82%);
}

.empty {
  width: 100%;
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #5b6070;
  font-weight: 600;
  text-align: center;
  word-break: break-all;
  padding: 0 16px;
}
</style>
