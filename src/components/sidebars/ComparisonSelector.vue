<template>
  <section class="panel-card">
    <div class="section-title">{{ source.label }}</div>
    <div class="field-row">
      <label>{{ t("comparisonDataset") }}</label>
      <select
        class="control-input"
        :value="source.dataset?.id || ''"
        @change="
          emit('datasetChange', ($event.target as HTMLSelectElement).value)
        "
      >
        <option
          v-for="dataset in datasets"
          :key="dataset.id"
          :value="dataset.id"
        >
          {{ dataset.label }}
        </option>
      </select>
    </div>
    <div class="field-row">
      <label>{{ t("comparisonRun") }}</label>
      <select
        class="control-input"
        :value="source.runId"
        @change="emit('runChange', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="!source.runs.length" value="">
          {{ t("comparisonNoRuns") }}
        </option>
        <option v-for="run in sortedRuns" :key="run.hash" :value="run.runId">
          {{ formatDate(run.dateMs) }} · {{ run.hash.slice(0, 8) }} ·
          {{ run.runId }}
        </option>
      </select>
    </div>
    <button
      class="paste-btn paste-row"
      type="button"
      :title="t('comparisonPasteHint')"
      @click="paste"
    >
      {{ t("comparisonPaste") }}
    </button>
    <button
      v-if="showSwap"
      class="comparison-swap-btn"
      type="button"
      @click="onSwap"
    >
      {{ t("comparisonSwapSources") }}
    </button>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type {
  ComparisonDataset,
  ComparisonSource,
  ComparisonSourceId,
} from "../../types/comparison";
import { formatDate } from "../../services/dataService";

const props = defineProps<{
  t: (key: string) => string;
  source: ComparisonSource;
  datasets: ComparisonDataset[];
  onPaste: (id: ComparisonSourceId) => Promise<void>;
  showSwap: boolean;
  onSwap: () => void;
}>();

const emit = defineEmits<{
  (e: "datasetChange", value: string): void;
  (e: "runChange", value: string): void;
  (e: "pasteError", value: string): void;
}>();

const sortedRuns = computed(() =>
  [...props.source.runs].sort((a, b) => Number(b.runId) - Number(a.runId)),
);

async function paste() {
  try {
    await props.onPaste(props.source.id);
  } catch (error) {
    emit(
      "pasteError",
      error instanceof Error
        ? error.message
        : props.t("comparisonClipboardError"),
    );
  }
}
</script>
