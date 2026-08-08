import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      // 开发环境保持浏览器同源请求，由 Vite 转发给 FastAPI。
      "/api": "http://127.0.0.1:8000",
    },
  },
});
