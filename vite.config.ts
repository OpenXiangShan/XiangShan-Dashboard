import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const repoBase = "./";

export default defineConfig({
  plugins: [vue()],
  base: process.env.VITE_BASE || repoBase,
});
