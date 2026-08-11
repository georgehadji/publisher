/**
 * Entry shim -- the application lives in src/app.ts (U6 split). Kept as
 * src/index.ts so the boot path (`tsx src/index.ts`, `node dist/index.js`)
 * does not change.
 */
import './app.js';
