#!/usr/bin/env node
/**
 * Schema codegen test — EXECUTES the generated types rather than grepping them.
 *
 * The previous version of this file claimed in its own docstring to validate "by
 * importing them", but only ran substring checks (`if (tsContent.includes('z.object'))`).
 * It therefore passed while the generated TypeScript contained 39 dangling references
 * and a literal `z.object({...PROPERTIES...})` syntax error, and while the generated
 * Python was an unimportable SyntaxError. A test that cannot fail is worse than no test:
 * it converts an outage into a green check.
 *
 * So this version actually runs the output:
 *   - TypeScript: strip type-only syntax, then EVALUATE the module against a mock `z`.
 *     A syntax error throws SyntaxError; a dangling `FooSchema` throws ReferenceError.
 *     This needs no node_modules, so it runs anywhere the generator runs.
 *   - Python: import the generated module in a subprocess with warnings-as-errors.
 *     Catches SyntaxError, NameError, and pydantic field-shadowing warnings.
 */
import { readFileSync, existsSync } from 'fs';
import { resolve, join } from 'path';
import { execFileSync } from 'child_process';

const ROOT = resolve(import.meta.dirname, '..');
const tsOut = join(ROOT, 'ts', 'index.gen.ts');
const pyOut = join(ROOT, 'py', 'models.gen.py');

let errors = 0;
const fail = (msg) => { console.error(`FAIL  ${msg}`); errors++; };
const pass = (msg) => console.log(`ok    ${msg}`);

// ── Mock zod ─────────────────────────────────────────────────────
// Every zod builder returns an endlessly chainable callable, so the generated module
// can be executed for real without installing zod.
function makeChainable() {
  const target = function () { return makeChainable(); };
  return new Proxy(target, {
    get(_t, prop) {
      if (prop === Symbol.toPrimitive || prop === 'toString') return () => '[zmock]';
      if (prop === 'then') return undefined; // never look thenable
      return makeChainable();
    },
    apply() { return makeChainable(); },
  });
}
const z = makeChainable();

// ── TypeScript ───────────────────────────────────────────────────
if (!existsSync(tsOut)) {
  fail('TS types not generated — run `node codegen/generate.mjs` first');
} else {
  const raw = readFileSync(tsOut, 'utf-8');

  if (raw.includes('PROPERTIES')) {
    fail('TS output contains the `{...PROPERTIES...}` placeholder — invalid TypeScript');
  } else {
    pass('TS output has no placeholder tokens');
  }

  // Strip TS-only constructs so the remainder is valid JS:
  //   - the zod import (we inject our mock instead)
  //   - `export type X = z.infer<...>;` lines (types have no runtime representation)
  //   - the `export ` keyword (we evaluate as a plain function body)
  //   - the `: z.ZodTypeAny` annotation on recursive schemas (a type annotation is not
  //     valid JS, and recursive zod schemas require one for tsc)
  const runtimeJs = raw
    .split('\n')
    .filter(l => !l.startsWith('import ') && !l.startsWith('export type '))
    .join('\n')
    .replace(/^export /gm, '')
    .replace(/^(const \w+): z\.ZodTypeAny =/gm, '$1 =');

  try {
    // eslint-disable-next-line no-new-func
    new Function('z', `"use strict";\n${runtimeJs}`)(z);
    pass('TS output executes — no syntax errors, no dangling schema references');
  } catch (err) {
    fail(`TS output failed to execute: ${err.constructor.name}: ${err.message}`);
  }

  const declared = new Set([...raw.matchAll(/export const (\w+Schema)(?::[^=]+)? =/g)].map(m => m[1]));
  if (declared.size === 0) fail('TS output declares no schemas at all');
  else pass(`TS output declares ${declared.size} runtime schemas`);
}

// ── Python ───────────────────────────────────────────────────────
if (!existsSync(pyOut)) {
  fail('Python types not generated — run `node codegen/generate.mjs` first');
} else {
  const probe = [
    'import warnings, sys, importlib.util',
    'warnings.simplefilter("error")',
    `spec = importlib.util.spec_from_file_location("models_gen", r"${pyOut}")`,
    'mod = importlib.util.module_from_spec(spec)',
    'sys.modules["models_gen"] = mod',
    'spec.loader.exec_module(mod)',
    'n = sum(1 for x in dir(mod) if x[:1].isupper())',
    'print(f"PYOK {n}")',
  ].join('\n');

  for (const exe of ['python', 'python3']) {
    try {
      const out = execFileSync(exe, ['-c', probe], { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] });
      const m = out.match(/PYOK (\d+)/);
      if (m) pass(`Python output imports cleanly (${m[1]} types, warnings-as-errors)`);
      else fail(`Python probe produced unexpected output: ${out.trim()}`);
      break;
    } catch (err) {
      if (err.code === 'ENOENT') continue; // try the next interpreter name
      fail(`Python output failed to import:\n${(err.stderr || err.message).toString().trim()}`);
      break;
    }
  }
}

console.log(`\n${errors === 0 ? 'All codegen checks passed' : `${errors} codegen check(s) failed`}`);
process.exit(errors > 0 ? 1 : 0);
