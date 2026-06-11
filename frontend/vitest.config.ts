import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./__tests__/setup.ts"],
    include: [
      "__tests__/**/*.test.{ts,tsx}",
      "**/__tests__/**/*.test.{ts,tsx}",
    ],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: [
        "services/**/*.ts",
        "hooks/**/*.ts",
        "store/**/*.ts",
        "lib/**/*.ts",
      ],
      exclude: ["node_modules/", "__tests__/", "**/*.d.ts", "**/*.config.*"],
    },
  },
});
