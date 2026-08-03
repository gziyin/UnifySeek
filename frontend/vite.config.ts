import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端端口：优先取环境变量（start.bat 会从 backend/.env 注入 VITE_BACKEND_PORT），
// 兜底 8000。避免前端代理与后端端口（用户可改为 7000 等）不一致。
const backendPort = process.env.VITE_BACKEND_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": `http://127.0.0.1:${backendPort}`,
      "/ws": {
        target: `ws://127.0.0.1:${backendPort}`,
        ws: true,
      },
    },
  },
});
