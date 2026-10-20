import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
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
      ...(mode === "outlook" &&
      env.RIVET_OUTLOOK_HTTPS_KEY &&
      env.RIVET_OUTLOOK_HTTPS_CERT
        ? {
            https: {
              key: readFileSync(env.RIVET_OUTLOOK_HTTPS_KEY),
              cert: readFileSync(env.RIVET_OUTLOOK_HTTPS_CERT),
            },
          }
        : {}),
      proxy: { "/api": "http://127.0.0.1:8787" },
    },
    build: {
      outDir:
        mode === "outlook"
          ? "dist-outlook"
          : mode === "workspace"
            ? "dist-workspace"
            : "dist",
      ...(mode === "outlook"
        ? {
            rollupOptions: {
              input: new URL("outlook/taskpane.html", import.meta.url).pathname,
            },
          }
        : {}),
    },
  };
});
