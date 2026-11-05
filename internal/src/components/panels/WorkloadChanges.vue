<template>
  <div class="workload-bars" aria-label="Workload IPC 变化柱状图">
    <div class="bar-axis" aria-hidden="true">
      <span></span
      ><span class="bar-axis-labels"
        ><span>−</span><span>0</span><span>+</span></span
      ><span>IPC 变化</span>
    </div>
    <div class="bar-list">
      <button
        v-for="row in rows"
        :key="row.name"
        type="button"
        class="bar-row"
        :data-workload="row.name"
        :class="{ selected: row.name === selected }"
        :aria-pressed="row.name === selected"
        :title="`共同 ${row.pair.count} 个切片`"
        @click="$emit('select', row.name)"
      >
        <span class="bar-label">{{ row.name }}</span>
        <span class="bar-track" aria-hidden="true"
          ><i
            v-if="row.change !== null"
            class="bar-fill"
            :class="row.change >= 0 ? 'positive' : 'negative'"
            :style="{
              [row.change >= 0 ? 'left' : 'right']: '50%',
              width: `${maximum ? (Math.abs(row.change) / maximum) * 50 : 0}%`,
            }"
          ></i
        ></span>
        <strong
          :class="
            row.change === null || Math.abs(row.change) <= 0.5
              ? 'neutral'
              : row.change > 0
                ? 'good'
                : 'bad'
          "
          >{{ formatPercent(row.change) }}</strong
        >
      </button>
      <div v-if="!rows.length" class="muted small">暂无符合筛选的 workload</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  rows: { name: string; change: number | null; pair: { count: number } }[];
  selected: string;
}>();
defineEmits<{ select: [workload: string] }>();
const maximum = computed(() =>
  Math.max(0, ...props.rows.map((row) => Math.abs(row.change ?? 0))),
);
const formatPercent = (value: number | null) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
</script>

<style scoped>
.bar-axis,
.bar-row {
  display: grid;
  grid-template-columns: 155px minmax(60px, 1fr) 90px;
  gap: 12px;
  align-items: center;
}
.bar-axis {
  padding: 0 8px 12px;
  color: var(--muted);
  font-size: 12px;
  text-align: right;
}
.bar-axis-labels {
  display: flex;
  justify-content: space-between;
}
.bar-list {
  max-height: 440px;
  overflow: auto;
  padding: 2px;
}
.bar-row {
  width: 100%;
  border: 0;
  border-radius: 8px;
  padding: 9px 8px;
  background: none;
  cursor: pointer;
  font-size: 12px;
  text-align: left;
  color: var(--text);
}
.bar-row:hover,
.bar-row.selected {
  background: #edf4ff;
}
.bar-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  position: relative;
  height: 18px;
  background: #f4f7fb;
  border-radius: 3px;
}
.bar-track::after {
  content: "";
  position: absolute;
  left: 50%;
  top: -5px;
  bottom: -5px;
  width: 1px;
  background: #b9c4d5;
}
.bar-fill {
  position: absolute;
  top: 2px;
  bottom: 2px;
  border-radius: 2px;
}
.bar-fill.positive {
  background: #19845d;
}
.bar-fill.negative {
  background: #c24b40;
}
.bar-row strong {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
@media (max-width: 600px) {
  .bar-axis,
  .bar-row {
    grid-template-columns: minmax(0, 1fr) minmax(60px, 1.4fr) 68px;
    gap: 8px;
  }
}
</style>
