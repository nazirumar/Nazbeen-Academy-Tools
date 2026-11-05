import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    // jsdom + userEvent interaction tests take several seconds on slow/loaded
    // developer machines (and on Windows without a warm Vite cache). Keep the
    // default at 5s for fast failures, but allow the known slow interaction
    // suites to finish instead of failing on CPU contention.
    testTimeout: 20000,
    // jsdom startup is expensive on Windows (~11-17s per file). Spawning one
    // worker per CPU can exceed Vitest's 60s worker-start timeout and abort
    // files that would otherwise pass. Two workers keeps isolation while
    // staying well inside that budget.
    maxWorkers: 2,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
