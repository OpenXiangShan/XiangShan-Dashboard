<template>
  <div class="panel-card benchmark-card">
    <div class="section-title">{{ t("testcasesTitle") }}</div>
    <div class="actions">
      <button
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'default' }"
        type="button"
        @click="$emit('selectPreset', 'default')"
      >
        {{ t("default") }}
      </button>
      <button
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'all' }"
        type="button"
        @click="$emit('selectPreset', 'all')"
      >
        {{ t("selectAll") }}
      </button>
      <button
        class="sidebar-btn"
        type="button"
        @click="$emit('clearSelection')"
      >
        {{ t("clear") }}
      </button>
      <button
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'geomean' }"
        type="button"
        @click="$emit('selectPreset', 'geomean')"
      >
        {{ t("geomean") }}
      </button>
      <button
        v-if="showSpecButtons"
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'int' }"
        type="button"
        @click="$emit('selectPreset', 'int')"
      >
        {{ t("specInt") }}
      </button>
      <button
        v-if="showSpecButtons"
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'fp' }"
        type="button"
        @click="$emit('selectPreset', 'fp')"
      >
        {{ t("specFp") }}
      </button>
    </div>
    <div class="list">
      <div
        v-for="name in benchmarks"
        :key="name"
        class="item"
        :class="{ selected: selected.includes(name) }"
        role="button"
        :aria-pressed="selected.includes(name)"
        tabindex="0"
        @click="$emit('toggleBenchmark', name)"
        @keydown.enter.prevent="$emit('toggleBenchmark', name)"
        @keydown.space.prevent="$emit('toggleBenchmark', name)"
      >
        <span>{{ name }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { QuickBenchmarkPreset } from "../../composables/useDashboardSettings";

defineProps<{
  t: (key: string) => string;
  benchmarks: string[];
  selected: string[];
  activeQuickPreset: QuickBenchmarkPreset | null;
  showSpecButtons: boolean;
}>();

defineEmits<{
  (e: "selectPreset", preset: QuickBenchmarkPreset): void;
  (e: "clearSelection"): void;
  (e: "toggleBenchmark", name: string): void;
}>();
</script>

<style scoped>
.benchmark-card {
  display: flex;
  flex-direction: column;
}

.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.list {
  max-height: 40vh;
  overflow-y: auto;
  overflow-x: hidden;
  border: 1px solid #e1e6ef;
  border-radius: 12px;
  padding: 8px 10px;
  background: #fcfdff;
  font-size: 13px;
}

.item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 4px;
  border-bottom: 1px dashed #eef1f6;
  cursor: pointer;
  border-radius: 8px;
}

.item:last-child {
  border-bottom: none;
}

.item.selected {
  background: #e6efff;
}

.item:not(.selected):hover {
  background: #f1f6ff;
}

.item.selected:hover {
  background: #d8e8ff;
}
</style>
