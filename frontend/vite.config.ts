import path from "path";
import fs from "fs";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const pkgJson = JSON.parse(
    fs.readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
  );
  const appVersion = pkgJson.version || "dev";
  const API_URL = "TERA_API_URL";
  return {
    base: "/",
    preview: {
      port: 5173,
      allowedHosts: ["TERA_APP_URL"],
    },
    server: {
      port: 5173,
      host: "0.0.0.0",
    },
    define: {
      "import.meta.env.VITE_APP_TAG": JSON.stringify(appVersion),
      "import.meta.env.VITE_API_URL": JSON.stringify(API_URL),
    },
    plugins: [react()],
    // The thumbnail worker (frontend/workers/thumbnailWorker.ts) is
    // constructed with { type: "module" } and pulls in three +
    // occt-import-js, so its bundle code-splits. Vite's default
    // worker.format of "iife" cannot emit multi-chunk worker output and
    // fails the build outright; "es" is required for module workers with
    // real dependencies like this one.
    worker: { format: "es" },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "."),
      },
    },
  };
});
