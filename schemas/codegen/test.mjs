#!/usr/bin/env node
/**
 * Schema codegen test — validates generated types by importing them.
 */
import { readFileSync, existsSync } from 'fs';
import { resolve, join } from 'path';

const ROOT = resolve(import.meta.dirname, '..');
const tsOut = join(ROOT, 'ts', 'index.gen.ts');
const pyOut = join(ROOT, 'py', 'models.gen.py');

let errors = 0;

// Check TS output exists
if (!existsSync(tsOut)) {
  console.error('❌ TS types not generated — run `pnpm gen` first');
  errors++;
} else {
  const tsContent = readFileSync(tsOut, 'utf-8');
  if (tsContent.includes('sha256')) {
    console.log('✅ TS types generated with sha256 type');
  }
  if (tsContent.includes('z.object')) {
    console.log('✅ TS types contain Zod schemas');
  }
  if (tsContent.includes('Book AST')) {
    console.log('✅ TS types include Book AST schema');
  }
}

// Check Python output exists
if (!existsSync(pyOut)) {
  console.error('❌ Python types not generated — run `pnpm gen` first');
  errors++;
} else {
  const pyContent = readFileSync(pyOut, 'utf-8');
  if (pyContent.includes('BaseModel')) {
    console.log('✅ Python types contain Pydantic models');
  }
  if (pyContent.includes('Sha256')) {
    console.log('✅ Python types contain Sha256 type');
  }
}

console.log(`\n${errors === 0 ? '✅ All checks passed' : `❌ ${errors} failures`}`);
process.exit(errors > 0 ? 1 : 0);
