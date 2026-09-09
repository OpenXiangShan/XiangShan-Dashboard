<template>
  <section class="panel-card">
    <div class="section-title">{{ t("coverage") }}</div>
    <select
      class="control-input"
      :value="selectedCoverageId"
      @change="
        $emit('coverageChange', ($event.target as HTMLSelectElement).value)
      "
    >
      <option
        v-for="coverage in coverages"
        :key="coverage.id"
        :value="coverage.id"
      >
        {{ coverage.label }}
      </option>
    </select>
  </section>
</template>

<script setup lang="ts">
import type { ComparisonCoverage } from "../../types/comparison";

defineProps<{
  t: (key: string) => string;
  coverages: ComparisonCoverage[];
  selectedCoverageId: string;
}>();

defineEmits<{
  (e: "coverageChange", value: string): void;
}>();
</script>
