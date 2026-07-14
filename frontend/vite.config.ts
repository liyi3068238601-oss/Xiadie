import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base 用相对路径，方便 Electron 以 file:// 加载打包后的前端
export default defineConfig({
  base: "./",
  plugins: [react()],
  // 固定 IPv4，避免 dev 期 vite 绑定到 ::1 而 Electron 用 127.0.0.1 连不上
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: {
        main: "index.html",
        pet: "pet.html",
      },
    },
  },
});
