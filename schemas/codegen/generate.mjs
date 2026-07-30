#!/usr/bin/env node
/**
 * Publisher Schema Codegen
 *
 * Reads JSON Schema files from schemas/ and generates:
 *   - TypeScript types (Zod) in schemas/ts/index.gen.ts
 *   - Python types (Pydantic) in schemas/py/models.gen.py
 *
 * Usage: node codegen/generate.mjs
 *
 * DESIGN NOTE — why this is a two-pass generator.
 *
 * The previous single-pass version emitted broken output in four distinct ways, all of
 * which came from the same root cause: it decided a type's NAME at the point of use
 * instead of registering types up front. That produced
 *   (a) a literal `z.object({...PROPERTIES...})` placeholder in the TS output when an
 *       object had no `properties` key — syntactically invalid TypeScript;
 *   (b) inline object types that were generated and then DISCARDED, leaving 39 dangling
 *       `Foo_BarSchema` references to types that were never declared;
 *   (c) Python enum members like `3D_RENDER` (from "3d-render") — a SyntaxError that made
 *       the whole generated module unimportable;
 *   (d) silent name collisions: enums were named after their PROPERTY, so preflight's
 *       `status` (pass/fail/warn/skip) overwrote manifest's `status`
 *       (completed/skipped/failed/cached), and `$defs` were merged into one global
 *       namespace so ast.schema.json's `sourceRef` was clobbered by
 *       cover/art-brief.schema.json's unrelated `sourceRef`.
 *
 * So: PASS 1 registers every named type (defs, inline objects, inline enums) into a
 * single registry, resolving collisions by content — same name + same content collapses
 * to one type; same name + DIFFERENT content gets qualified with its owning document.
 * PASS 2 emits from the registry in dependency order. Names are therefore decided once,
 * consistently, for both language targets.
 */

import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from 'fs';
import { resolve, basename, join } from 'path';
import { createHash } from 'crypto';

const ROOT = resolve(import.meta.dirname, '..');
const SCHEMA_DIRS = [
  'ast', 'overrides', 'designspec', 'profile', 'preflight',
  'manifest', 'classification', 'agent-proposal', 'pagemap', 'cover', 'ts'
];
const OUT_TS = join(ROOT, 'ts');
const OUT_PY = join(ROOT, 'py');

for (const d of [OUT_TS, OUT_PY]) {
  if (!existsSync(d)) mkdirSync(d, { recursive: true });
}

// ── Helpers ──────────────────────────────────────────────────────

function toPascalCase(str) {
  return String(str)
    .replace(/[-_./ ](\w)/g, (_, c) => c.toUpperCase())
    .replace(/^[a-z]/, c => c.toUpperCase());
}

function contentHash(node) {
  return createHash('sha256').update(JSON.stringify(node, Object.keys(node).sort())).digest('hex').slice(0, 12);
}

/**
 * Sanitize a value into a valid Python enum member name.
 * Python identifiers may not start with a digit — "3d-render" must not become
 * `3D_RENDER`. Prefix those with `V_` rather than dropping the character, so
 * "3d-render" and "d-render" cannot collapse onto the same member.
 */
function toPyEnumMember(value) {
  let s = String(value).toUpperCase().replace(/[^A-Z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  if (!s) s = 'EMPTY';
  if (/^[0-9]/.test(s)) s = `V_${s}`;
  return s;
}

// ── PASS 1: build the type registry ──────────────────────────────

/**
 * registry: Map<typeName, { name, kind, node, doc }>
 *   kind: 'object' | 'enum' | 'union' | 'alias'
 * refMap: Map<`${docName}#${defName}`, typeName>  — resolves a $ref within its document
 */
function buildRegistry(schemas) {
  const registry = new Map();
  const refMap = new Map();

  // Step 1a: group every $defs entry by name, then by content, to detect real collisions.
  // A name used by several documents with IDENTICAL content is one shared type. The same
  // name with DIFFERENT content is several types and must be qualified — otherwise one
  // silently clobbers the other (this is what happened to ast's `sourceRef`).
  const byName = new Map();
  for (const s of schemas) {
    for (const [defName, def] of Object.entries(s.content.$defs || {})) {
      if (!byName.has(defName)) byName.set(defName, []);
      byName.get(defName).push({ doc: s, defName, def, hash: contentHash(def) });
    }
  }

  for (const [defName, occurrences] of byName) {
    const distinct = new Set(occurrences.map(o => o.hash));
    const needsQualifying = distinct.size > 1;
    for (const occ of occurrences) {
      const typeName = needsQualifying
        ? `${toPascalCase(occ.doc.name)}_${toPascalCase(defName)}`
        : toPascalCase(defName);
      refMap.set(`${occ.doc.name}#${defName}`, typeName);
      if (!registry.has(typeName)) {
        registry.set(typeName, { name: typeName, kind: kindOf(occ.def), node: occ.def, doc: occ.doc });
      }
    }
  }

  // Step 1b: register top-level document types.
  for (const s of schemas) {
    if (!s.content.properties) continue;
    const typeName = toPascalCase(s.content.title || s.name);
    if (!registry.has(typeName)) {
      registry.set(typeName, { name: typeName, kind: 'object', node: s.content, doc: s });
    }
  }

  // Step 1c: walk every registered type and hoist inline objects / inline enums into
  // their own registered types. Iterating over a growing list is intentional — hoisted
  // types are themselves walked, so nesting of any depth is handled.
  const queue = [...registry.values()];
  while (queue.length) {
    const entry = queue.shift();
    if (entry.kind !== 'object') continue;
    for (const [key, prop] of Object.entries(entry.node.properties || {})) {
      hoist(prop, entry, key, registry, queue);
    }
  }

  return { registry, refMap };
}

function kindOf(node) {
  if (node.oneOf || node.anyOf) return 'union';
  if (node.type === 'string' && node.enum) return 'enum';
  if (node.type === 'object' || node.properties) return 'object';
  return 'alias';
}

/**
 * If `prop` is an inline object-with-properties or an inline string enum, register it as
 * a named type derived from its parent and key (`Parent_Key`), and record that name on
 * the property node so both emitters resolve to the identical name.
 */
function hoist(prop, parentEntry, key, registry, queue) {
  if (!prop || typeof prop !== 'object') return;

  if (prop.type === 'array' && prop.items) {
    hoist(prop.items, parentEntry, `${key}Item`, registry, queue);
    return;
  }

  const isInlineObject = (prop.type === 'object' || prop.properties) && prop.properties && !prop.$ref;
  const isInlineEnum = prop.type === 'string' && Array.isArray(prop.enum) && !prop.$ref;
  if (!isInlineObject && !isInlineEnum) return;

  const baseName = `${parentEntry.name}_${toPascalCase(key)}`;
  const hash = contentHash(prop);

  // Reuse an identical previously-hoisted type rather than emitting a duplicate.
  for (const existing of registry.values()) {
    if (existing.hoisted && existing.hash === hash && existing.name === baseName) {
      prop.__typeName = existing.name;
      return;
    }
  }

  let typeName = baseName;
  let n = 2;
  while (registry.has(typeName) && contentHash(registry.get(typeName).node) !== hash) {
    typeName = `${baseName}${n++}`;
  }

  if (!registry.has(typeName)) {
    const entry = {
      name: typeName,
      kind: isInlineEnum ? 'enum' : 'object',
      node: prop,
      doc: parentEntry.doc,
      hoisted: true,
      hash,
    };
    registry.set(typeName, entry);
    queue.push(entry);
  }
  prop.__typeName = typeName;
}

/** Resolve a $ref to its registered type name, scoped to the document that declared it. */
function resolveRef(ref, doc, refMap) {
  const defName = ref.replace('#/$defs/', '');
  return refMap.get(`${doc.name}#${defName}`) || toPascalCase(defName);
}

// ── PASS 2a: TypeScript / Zod emitter ────────────────────────────

/**
 * Types that participate in a reference cycle (the AST is genuinely recursive: an
 * inlineNode contains inlineNodes). A plain `const A = z.object({ child: BSchema })`
 * where B refers back to A is a runtime "Cannot access before initialization" error, so
 * every reference INTO a cyclic type is wrapped in `z.lazy()`. Populated by
 * orderByDependency() before emission.
 */
const CYCLIC_TYPES = new Set();

function tsRef(typeName) {
  return CYCLIC_TYPES.has(typeName) ? `z.lazy(() => ${typeName}Schema)` : `${typeName}Schema`;
}

function tsExpr(node, doc, refMap) {
  if (!node) return 'z.unknown()';
  if (node.__typeName) return tsRef(node.__typeName);
  if (node.$ref) return tsRef(resolveRef(node.$ref, doc, refMap));

  if (node.oneOf) return `z.union([${node.oneOf.map(s => tsExpr(s, doc, refMap)).join(', ')}])`;
  if (node.anyOf) return `z.union([${node.anyOf.map(s => tsExpr(s, doc, refMap)).join(', ')}])`;
  if (node.allOf) return node.allOf.map(s => tsExpr(s, doc, refMap)).join('.and(') + ')'.repeat(node.allOf.length - 1);

  if (node.type === 'array') {
    let s = `z.array(${tsExpr(node.items, doc, refMap)})`;
    if (node.minItems !== undefined) s += `.min(${node.minItems})`;
    if (node.maxItems !== undefined) s += `.max(${node.maxItems})`;
    return s;
  }

  if (node.type === 'object' || node.properties) {
    if (node.additionalProperties && typeof node.additionalProperties === 'object') {
      return `z.record(z.string(), ${tsExpr(node.additionalProperties, doc, refMap)})`;
    }
    // An object with no declared properties is genuinely unconstrained. Emit a real
    // permissive type — never a placeholder token (this was bug (a)).
    return 'z.record(z.string(), z.unknown())';
  }

  if (node.type === 'string') {
    if (node.enum) return `z.enum([${node.enum.map(e => JSON.stringify(e)).join(', ')}])`;
    if (node.pattern) return `z.string().regex(new RegExp(${JSON.stringify(node.pattern)}))`;
    if (node.format === 'uri' || node.format === 'uri-reference') return 'z.string().url()';
    if (node.format === 'date-time') return 'z.string().datetime()';
    let s = 'z.string()';
    if (node.minLength !== undefined) s += `.min(${node.minLength})`;
    if (node.maxLength !== undefined) s += `.max(${node.maxLength})`;
    return s;
  }

  if (node.type === 'integer') {
    let s = 'z.number().int()';
    if (node.minimum !== undefined) s += `.min(${node.minimum})`;
    if (node.maximum !== undefined) s += `.max(${node.maximum})`;
    return s;
  }

  if (node.type === 'number') {
    let s = 'z.number()';
    if (node.minimum !== undefined) s += `.min(${node.minimum})`;
    if (node.maximum !== undefined) s += `.max(${node.maximum})`;
    return s;
  }

  if (node.type === 'boolean') return 'z.boolean()';
  if (Array.isArray(node.type)) {
    return `z.union([${node.type.map(t => tsExpr({ ...node, type: t }, doc, refMap)).join(', ')}])`;
  }
  return 'z.unknown()';
}

function emitTsType(entry, refMap) {
  const { name, kind, node, doc } = entry;
  const lines = [];
  // A recursive schema cannot have its type inferred structurally — tsc needs an
  // explicit annotation to break the inference cycle.
  const ann = CYCLIC_TYPES.has(name) ? ': z.ZodTypeAny' : '';

  if (kind === 'enum') {
    lines.push(`export const ${name}Schema${ann} = z.enum([${node.enum.map(e => JSON.stringify(e)).join(', ')}]);`);
    lines.push(`export type ${name} = z.infer<typeof ${name}Schema>;`);
    return lines.join('\n');
  }

  if (kind === 'union') {
    const variants = (node.oneOf || node.anyOf).map(v => tsExpr(v, doc, refMap));
    lines.push(`export const ${name}Schema${ann} = z.union([${variants.join(', ')}]);`);
    lines.push(`export type ${name} = z.infer<typeof ${name}Schema>;`);
    return lines.join('\n');
  }

  if (kind === 'object') {
    const props = node.properties || {};
    const required = new Set(node.required || []);
    lines.push(`export const ${name}Schema${ann} = z.object({`);
    for (const [key, prop] of Object.entries(props)) {
      const expr = tsExpr(prop, doc, refMap);
      const opt = required.has(key) ? '' : '.optional()';
      lines.push(`  ${JSON.stringify(key)}: ${expr}${opt},`);
    }
    lines.push('});');
    lines.push(`export type ${name} = z.infer<typeof ${name}Schema>;`);
    return lines.join('\n');
  }

  lines.push(`export const ${name}Schema${ann} = ${tsExpr(node, doc, refMap)};`);
  lines.push(`export type ${name} = z.infer<typeof ${name}Schema>;`);
  return lines.join('\n');
}

// ── PASS 2b: Python / Pydantic emitter ───────────────────────────

function pyExpr(node, doc, refMap) {
  if (!node) return 'Any';
  if (node.__typeName) return node.__typeName;
  if (node.$ref) return resolveRef(node.$ref, doc, refMap);

  if (node.oneOf) return `Union[${node.oneOf.map(s => pyExpr(s, doc, refMap)).join(', ')}]`;
  if (node.anyOf) return `Union[${node.anyOf.map(s => pyExpr(s, doc, refMap)).join(', ')}]`;
  if (node.type === 'array') return `list[${pyExpr(node.items, doc, refMap)}]`;

  if (node.type === 'object' || node.properties) {
    if (node.additionalProperties && typeof node.additionalProperties === 'object') {
      return `dict[str, ${pyExpr(node.additionalProperties, doc, refMap)}]`;
    }
    return 'dict[str, Any]';
  }

  if (node.type === 'string') {
    if (node.format === 'date-time') return 'datetime';
    return 'str';
  }
  if (node.type === 'integer') return 'int';
  if (node.type === 'number') return 'float';
  if (node.type === 'boolean') return 'bool';
  return 'Any';
}

function emitPyType(entry, refMap) {
  const { name, kind, node, doc } = entry;
  const lines = [];

  if (kind === 'enum') {
    lines.push(`class ${name}(str, Enum):`);
    const usedMembers = new Set();
    for (const v of node.enum) {
      let member = toPyEnumMember(v);
      // Two distinct values must never collapse onto one member name.
      let n = 2;
      while (usedMembers.has(member)) member = `${toPyEnumMember(v)}_${n++}`;
      usedMembers.add(member);
      lines.push(`    ${member} = ${JSON.stringify(v)}`);
    }
    lines.push('');
    return lines.join('\n');
  }

  if (kind === 'union') {
    const variants = (node.oneOf || node.anyOf).map(v => pyExpr(v, doc, refMap));
    lines.push(`${name} = Union[${variants.join(', ')}]`);
    lines.push('');
    return lines.join('\n');
  }

  if (kind !== 'object') {
    lines.push(`${name} = ${pyExpr(node, doc, refMap)}`);
    lines.push('');
    return lines.join('\n');
  }

  const props = node.properties || {};
  const required = new Set(node.required || []);
  lines.push(`class ${name}(BaseModel):`);

  const body = [];
  const validators = [];
  let needsAliasConfig = false;

  for (const [key, prop] of Object.entries(props)) {
    const pyType = pyExpr(prop, doc, refMap);
    const isReq = required.has(key);
    const safeKey = isSafePyField(key) ? key : `${key.replace(/[^A-Za-z0-9_]/g, '_')}_`;
    const aliased = safeKey !== key;
    if (aliased) needsAliasConfig = true;

    const aliasArg = aliased ? `alias=${JSON.stringify(key)}` : '';
    if (isReq) {
      body.push(`    ${safeKey}: ${pyType}` + (aliased ? ` = Field(..., ${aliasArg})` : ''));
    } else {
      const args = ['default=None', aliasArg].filter(Boolean).join(', ');
      body.push(`    ${safeKey}: Optional[${pyType}] = Field(${args})`);
    }

    if (prop.pattern && prop.type === 'string') {
      validators.push(
        `\n    @field_validator(${JSON.stringify(safeKey)})` +
        `\n    @classmethod` +
        `\n    def _validate_${safeKey}(cls, v: Optional[str]) -> Optional[str]:` +
        `\n        if v is not None and not re.match(${JSON.stringify(prop.pattern)}, v):` +
        `\n            raise ValueError(${JSON.stringify(`${key} does not match required pattern`)})` +
        `\n        return v`
      );
    }
  }

  // Aliased fields must remain constructible by BOTH the Python-safe name and the
  // original JSON name, or round-tripping a validated document breaks.
  if (needsAliasConfig) {
    lines.push('    model_config = ConfigDict(populate_by_name=True)');
    lines.push('');
  }

  if (!body.length) body.push('    pass');
  lines.push(...body);
  if (validators.length) lines.push(...validators);
  lines.push('');
  return lines.join('\n');
}

/**
 * Field names that cannot be emitted verbatim as Python attributes. Two distinct
 * problems, one fix (suffix with `_` and carry the real name as a pydantic alias):
 *   - Python KEYWORDS (`from`, `class`, ...) are a hard SyntaxError. JSON Schema happily
 *     allows a property named "from"; Python does not.
 *   - pydantic BaseModel ATTRIBUTES (`schema`, `json`, `copy`, ...) are merely shadowed,
 *     which pydantic reports as a UserWarning and which makes the field awkward to reach.
 */
const PY_KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class',
  'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global',
  'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise',
  'return', 'try', 'while', 'with', 'yield', 'match', 'case', 'type',
]);
const PY_BASEMODEL_ATTRS = new Set([
  'schema', 'copy', 'json', 'dict', 'construct', 'fields', 'validate',
  'model_config', 'model_fields', 'model_dump', 'model_dump_json', 'model_rebuild',
]);
const PY_RESERVED_FIELDS = new Set([...PY_KEYWORDS, ...PY_BASEMODEL_ATTRS]);

/** A property name is only emittable as-is if it is also a valid Python identifier. */
function isSafePyField(key) {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(key) && !PY_RESERVED_FIELDS.has(key);
}

// ── Dependency ordering ──────────────────────────────────────────

/**
 * Emit dependencies before dependents. TypeScript `const` declarations are evaluated in
 * order, so a forward reference is a runtime ReferenceError, not just a lint warning.
 */
function orderByDependency(registry, refMap) {
  const deps = new Map();
  for (const [name, entry] of registry) {
    deps.set(name, collectDeps(entry.node, entry.doc, refMap, new Set()));
  }

  const ordered = [];
  const state = new Map(); // name -> 'visiting' | 'done'

  function visit(name) {
    if (state.get(name) === 'done') return;
    if (state.get(name) === 'visiting') {
      // Back edge: `name` is its own (possibly indirect) dependency. Record it so every
      // reference to it is emitted through z.lazy(), then break the recursion.
      CYCLIC_TYPES.add(name);
      return;
    }
    state.set(name, 'visiting');
    for (const dep of deps.get(name) || []) {
      if (registry.has(dep) && dep !== name) visit(dep);
      else if (dep === name) CYCLIC_TYPES.add(name); // direct self-reference
    }
    state.set(name, 'done');
    ordered.push(registry.get(name));
  }

  for (const name of registry.keys()) visit(name);
  return ordered;
}

function collectDeps(node, doc, refMap, acc) {
  if (!node || typeof node !== 'object') return acc;
  if (node.__typeName) acc.add(node.__typeName);
  if (node.$ref) acc.add(resolveRef(node.$ref, doc, refMap));
  for (const key of ['properties', 'items', 'oneOf', 'anyOf', 'allOf', 'additionalProperties']) {
    const child = node[key];
    if (!child || typeof child !== 'object') continue;
    if (Array.isArray(child)) child.forEach(c => collectDeps(c, doc, refMap, acc));
    else if (key === 'properties') Object.values(child).forEach(c => collectDeps(c, doc, refMap, acc));
    else collectDeps(child, doc, refMap, acc);
  }
  return acc;
}

// ── Drivers ──────────────────────────────────────────────────────

function collectSchemas() {
  const schemas = [];
  for (const dir of SCHEMA_DIRS) {
    const dirPath = join(ROOT, dir);
    if (!existsSync(dirPath)) continue;
    for (const entry of readdirSync(dirPath)) {
      if (!entry.endsWith('.schema.json')) continue;
      const content = JSON.parse(readFileSync(join(dirPath, entry), 'utf-8'));
      schemas.push({
        name: content.title || basename(entry, '.schema.json'),
        dir, file: entry, content,
      });
    }
  }
  return schemas;
}

function main() {
  const schemas = collectSchemas();
  console.log(`Found ${schemas.length} schema files`);

  const { registry, refMap } = buildRegistry(schemas);
  const ordered = orderByDependency(registry, refMap);
  console.log(`Registered ${registry.size} types (${ordered.filter(e => e.hoisted).length} hoisted from inline definitions)`);

  const tsLines = [
    '// Auto-generated from JSON Schema — do not edit manually.',
    '// Run `node codegen/generate.mjs` from schemas/ to regenerate.',
    '',
    "import { z } from 'zod';",
    '',
  ];
  for (const entry of ordered) tsLines.push(emitTsType(entry, refMap), '');
  writeFileSync(join(OUT_TS, 'index.gen.ts'), tsLines.join('\n'), 'utf-8');
  console.log(`Generated TS types → ${join(OUT_TS, 'index.gen.ts')}`);

  const pyLines = [
    '# Auto-generated from JSON Schema — do not edit manually.',
    '# Run `node codegen/generate.mjs` from schemas/ to regenerate.',
    '',
    'from __future__ import annotations',
    'from datetime import datetime',
    'from enum import Enum',
    'from pydantic import BaseModel, ConfigDict, Field, field_validator',
    'from typing import Any, Optional, Union',
    'import re',
    '',
    '',
  ];
  for (const entry of ordered) pyLines.push(emitPyType(entry, refMap), '');
  // Rebuild forward references created by `from __future__ import annotations`.
  pyLines.push('');
  pyLines.push('for _name, _obj in list(globals().items()):');
  pyLines.push('    if isinstance(_obj, type) and issubclass(_obj, BaseModel):');
  pyLines.push('        try:');
  pyLines.push('            _obj.model_rebuild()');
  pyLines.push('        except Exception:');
  pyLines.push('            pass');
  pyLines.push('');
  writeFileSync(join(OUT_PY, 'models.gen.py'), pyLines.join('\n'), 'utf-8');
  console.log(`Generated Python types → ${join(OUT_PY, 'models.gen.py')}`);

  console.log('Codegen complete.');
}

main();
