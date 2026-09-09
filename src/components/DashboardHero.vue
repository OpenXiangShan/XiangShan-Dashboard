<template>
  <div class="hero panel-surface">
    <div class="hero-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="tab-btn"
        :class="{ active: selectedTabId === tab.id }"
        type="button"
        @click="$emit('tabChange', tab.id)"
      >
        {{ t(tab.titleKey) }}
      </button>
    </div>
    <div class="badges">
      <div v-if="showBadges" class="badge">
        {{ t("runsLabel") }}: {{ runCount }}
      </div>
      <div v-if="showBadges || showBenchmarkBadge" class="badge">
        {{ t("testcasesLabel") }}: {{ benchmarkCount }}
      </div>
      <div class="badge">
        {{ t("versionLabel") }}: {{ version }}-{{ commitHash }}
      </div>
      <div class="badge">{{ t("buildLabel") }}: {{ buildTimeText }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { TabConfig } from "../config/tabs";
import { formatDate } from "../services/dataService";

const props = defineProps<{
  tabs: TabConfig[];
  selectedTabId: string;
  t: (key: string) => string;
  version: string;
  commitHash: string;
  buildTimestamp: number;
  runCount: number;
  benchmarkCount: number;
  showBadges?: boolean;
  showBenchmarkBadge?: boolean;
}>();

const buildDate = new Date(props.buildTimestamp);
const buildTimeText = `${formatDate(buildDate)} ${String(buildDate.getHours()).padStart(2, "0")}:${String(buildDate.getMinutes()).padStart(2, "0")}`;

defineEmits<{
  (e: "tabChange", tabId: string): void;
}>();
</script>

<style scoped>
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
}

.hero-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tab-btn {
  border: 1px solid #dce5f6;
  border-radius: var(--radius-control);
  padding: 8px 12px;
  background: #f7f9ff;
  color: #31435f;
  cursor: pointer;
  font-weight: 700;
}

.tab-btn.active {
  background: #3a7ff6;
  color: #ffffff;
  border-color: #3a7ff6;
}

.badges {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.badge {
  padding: 6px 10px;
  background: #e6efff;
  color: #3a7ff6;
  border-radius: var(--radius-control);
  font-weight: 700;
  font-size: 12px;
}
</style>
