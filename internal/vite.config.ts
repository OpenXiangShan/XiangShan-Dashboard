import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [vue()],
  base: "./",
  resolve: {
    alias: {
      "@comparison": fileURLToPath(new URL("./comparison.js", import.meta.url)),
    },
  },
  optimizeDeps: { include: ["@comparison"] },
  build: { outDir: "../dist-internal", emptyOutDir: true },
});
