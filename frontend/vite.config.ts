import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import path from "node:path";

// backend/.env 目录（由本文件位置推导，与 cwd 无关）。
const backendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../backend");

export default defineConfig(({ mode }) => {
  // 后端端口优先级：start.bat 注入的 VITE_BACKEND_PORT → backend/.env 的 APP_PORT → 8000。
  // loadEnv 前缀限定为 "APP"，只读取 APP_* 键，避免 DEEPSEEK/TAVILY 等真实密钥进入 vite 进程。
  const backendPort =
    process.env.VITE_BACKEND_PORT || loadEnv(mode, backendDir, "APP").APP_PORT || "8000";

  return {
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
  };
});
