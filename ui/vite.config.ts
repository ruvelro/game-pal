import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, la GUI corre en Vite y habla con el daemon por proxy. `game-pal --dev` indica su puerto.
const daemon = `http://127.0.0.1:${process.env.GAMEPAL_DAEMON_PORT ?? "47821"}`;

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
      // El daemon solo acepta el WebSocket de eventos desde su propio origen. Reescribirlo no abre la puerta a
      // otras webs: el WebSocket sigue exigiendo el token de sesión como primer mensaje.
      "/api": { target: daemon, ws: true, changeOrigin: true, rewriteWsOrigin: true },
    },
  },
});
