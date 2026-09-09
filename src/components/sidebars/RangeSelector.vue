<template>
  <div class="panel-card">
    <div class="section-title">{{ t("filtersTitle") }}</div>
    <div class="row">
      <label>{{ t("branch") }}</label>
      <select
        class="control-input"
        :value="selectedBranch"
        @change="
          $emit('branchChange', ($event.target as HTMLSelectElement).value)
        "
      >
        <option v-for="branch in branches" :key="branch" :value="branch">
          {{ branch }}
        </option>
      </select>
    </div>
    <div v-if="subsets.length" class="row">
      <label>{{ t("subset") }}</label>
      <select
        class="control-input"
        :value="selectedSubset"
        @change="
          $emit('subsetChange', ($event.target as HTMLSelectElement).value)
        "
      >
        <option v-for="subset in subsets" :key="subset" :value="subset">
          {{ subset }}
        </option>
      </select>
    </div>
    <div class="row">
      <label>{{ t("startDate") }}</label>
      <input
        class="control-input"
        :class="{ 'input-inactive': activeQuickPreset === 'lastTenRuns' }"
        type="date"
        :value="startDateStr"
        @change="
          $emit('startDateChange', ($event.target as HTMLInputElement).value)
        "
      />
    </div>
    <div class="row">
      <label>{{ t("endDate") }}</label>
      <input
        class="control-input"
        :class="{ 'input-inactive': activeQuickPreset === 'lastTenRuns' }"
        type="date"
        :value="endDateStr"
        @change="
          $emit('endDateChange', ($event.target as HTMLInputElement).value)
        "
      />
    </div>
    <div class="btn-row">
      <button
        class="sidebar-btn"
        v-if="tab.id !== 'score-weekly'"
        :class="{ active: activeQuickPreset === 'lastWeek' }"
        type="button"
        @click="$emit('setQuickPreset', 'lastWeek')"
      >
        {{ t("lastWeek") }}
      </button>
      <button
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'lastMonth' }"
        type="button"
        @click="$emit('setQuickPreset', 'lastMonth')"
      >
        {{ t("lastMonth") }}
      </button>
      <button
        class="sidebar-btn"
        v-if="tab.id === 'score-weekly'"
        :class="{ active: activeQuickPreset === 'last3Months' }"
        type="button"
        @click="$emit('setQuickPreset', 'last3Months')"
      >
        {{ t("last3Months") }}
      </button>
      <button
        class="sidebar-btn"
        :class="{ active: activeQuickPreset === 'lastTenRuns' }"
        type="button"
        @click="$emit('setQuickPreset', 'lastTenRuns')"
      >
        {{ t("lastTenRuns") }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { QuickRangePreset } from "../../composables/useDashboardSettings";
import { ChartConfig } from "../../config/tabs";

defineProps<{
  t: (key: string) => string;
  tab: ChartConfig;
  subsets: readonly string[];
  branches: string[];
  selectedBranch: string;
  selectedSubset: string;
  startDateStr: string;
  endDateStr: string;
  activeQuickPreset: QuickRangePreset | null;
}>();

defineEmits<{
  (e: "branchChange", value: string): void;
  (e: "subsetChange", value: string): void;
  (e: "startDateChange", value: string): void;
  (e: "endDateChange", value: string): void;
  (e: "setQuickPreset", preset: QuickRangePreset): void;
}>();
</script>

<style scoped>
.row {
  margin-bottom: 10px;
  display: flex;
  gap: 10px;
  align-items: center;
}

.btn-row {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

.input-inactive {
  opacity: 0.55;
  border-style: dashed;
}
</style>
