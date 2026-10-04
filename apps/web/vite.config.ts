import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { createApiRouting } from "./src/api-routing.ts";
const root = new URL(".", import.meta.url).pathname;
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, root, "");
  if (env.VITE_API_URL)
    createApiRouting(
      env.VITE_API_URL,
      env.VERCEL ? "https://rivet.vercel.app" : "http://localhost:5178",
    );
  return {
    root,
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5178,
      strictPort: true,
      proxy: { "/api": "http://127.0.0.1:8787" },
    },
    build: { outDir: "dist" },
  };
});
