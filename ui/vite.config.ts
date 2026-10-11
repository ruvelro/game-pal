import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, la GUI corre en Vite y habla con el daemon por proxy.
const daemon = "http://127.0.0.1:47821";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../gamepal/gui_server/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api/events": { target: daemon.replace("http", "ws"), ws: true, changeOrigin: true },
      "/api": { target: daemon, changeOrigin: true },
    },
  },
});
