import { reactive } from "vue";
import { version as appVersion } from "../../package.json";

export type QuickRangePreset =
  | "lastWeek"
  | "lastMonth"
  | "last3Months"
  | "lastTenRuns";

export interface DashboardSettings {
  selectedBranch: string;
  startDateStr: string;
  endDateStr: string;
  quickRangePreset: QuickRangePreset | null;
  selectedBenchmarks: string[];
  selectedTabId: string;
  selectedSubset: string;
}

const settingsKey = "xs-dashboard-settings";
const settingsVersionKey = `${settingsKey}-version`;

function createDefaultSettings(): DashboardSettings {
  return {
    selectedBranch: "",
    startDateStr: "",
    endDateStr: "",
    quickRangePreset: null,
    selectedBenchmarks: [],
    selectedTabId: "ipc-commit",
    selectedSubset: "",
  };
}

export function useDashboardSettings() {
  const state = reactive<DashboardSettings>(createDefaultSettings());

  function clear() {
    localStorage.clear();
    Object.assign(state, createDefaultSettings());
  }

  function load() {
    try {
      if (localStorage.getItem(settingsVersionKey) !== appVersion) {
        clear();
        return;
      }
      const saved = localStorage.getItem(settingsKey);
      if (!saved) return;
      Object.assign(state, JSON.parse(saved) as DashboardSettings);
    } catch (err) {
      console.warn("Failed to load settings", err);
      clear();
    }
  }

  function save() {
    localStorage.setItem(settingsKey, JSON.stringify(state));
    localStorage.setItem(settingsVersionKey, appVersion);
  }

  return {
    state,
    load,
    save,
  };
}
