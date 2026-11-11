import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import path from "node:path";
import pkg from "./package.json" with { type: "json" };
const { name, version } = pkg;

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify("test"),
    __APP_NAME__: JSON.stringify("test"),
    __APP_INFO__: JSON.stringify({ pkg: { name, version } }),
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@views": path.resolve(import.meta.dirname, "src/views"),
      "@imgs": path.resolve(import.meta.dirname, "src/assets/images"),
      "@icons": path.resolve(import.meta.dirname, "src/assets/images/svg"),
      "@utils": path.resolve(import.meta.dirname, "src/utils"),
      "@stores": path.resolve(import.meta.dirname, "src/store"),
      "@plugins": path.resolve(import.meta.dirname, "src/plugins"),
      "@styles": path.resolve(import.meta.dirname, "src/styles"),
      "@api": path.resolve(import.meta.dirname, "src/api"),
      "@fa_imgs": path.resolve(import.meta.dirname, "src/assets/fa_imgs"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{ts,js}"],
  },
});
