import { execFileSync } from "node:child_process";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const repoBase = "./";

function getCommitHash(): string {
  try {
    return execFileSync("git", ["rev-parse", "--short=8", "HEAD"], {
      encoding: "utf8",
    }).trim();
  } catch {
    return process.env.GITHUB_SHA?.slice(0, 8) || "unknown";
  }
}

export default defineConfig({
  plugins: [vue()],
  base: process.env.VITE_BASE || repoBase,
  define: {
    __COMMIT_HASH__: JSON.stringify(getCommitHash()),
    __BUILD_TIMESTAMP__: JSON.stringify(Date.now()),
  },
});
