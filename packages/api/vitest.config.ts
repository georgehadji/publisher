import { defineConfig } from 'vitest/config';

// vitest 5 no longer leaves `dist/` out by default, and `tsc` compiles the
// tests there too: without this the stale compiled copies run beside src.
export default defineConfig({
  test: { include: ['src/**/*.test.ts'] },
});
