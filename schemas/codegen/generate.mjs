#!/usr/bin/env node
/**
 * Publisher Schema Codegen
 *
 * Reads JSON Schema files from schemas/ and generates:
 *   - TypeScript types (Zod) in schemas/ts/*.gen.ts
 *   - Python types (Pydantic) in schemas/py/*.gen.py
 *
 * Usage: node codegen/generate.mjs
 */

import { readFileSync, writeFileSync, readdirSync, statSync, existsSync, mkdirSync } from 'fs';
import { resolve, dirname, basename, extname, join } from 'path';

const ROOT = resolve(import.meta.dirname, '..');
const SCHEMA_DIRS = [
  'ast', 'overrides', 'designspec', 'profile', 'preflight',
  'manifest', 'classification', 'agent-proposal', 'pagemap', 'ts'
];
const OUT_TS = join(ROOT, 'ts');
const OUT_PY = join(ROOT, 'py');

// Ensure output directories exist
for (const d of [OUT_TS, OUT_PY]) {
  if (!existsSync(d)) mkdirSync(d, { recursive: true });
}

function collectSchemas() {
  const schemas = [];
  for (const dir of SCHEMA_DIRS) {
    const dirPath = join(ROOT, dir);
    if (!existsSync(dirPath)) continue;
    const entries = readdirSync(dirPath);
    for (const entry of entries) {
      if (!entry.endsWith('.schema.json')) continue;
      const fullPath = join(dirPath, entry);
      const content = JSON.parse(readFileSync(fullPath, 'utf-8'));
      schemas.push({ name: content.title || basename(entry, '.schema.json'), dir, file: entry, content });
    }
  }
  return schemas;
}

function toPascalCase(str) {
  return str.replace(/[-_./ ](\w)/g, (_, c) => c.toUpperCase()).replace(/^[a-z]/, c => c.toUpperCase());
}

function toCamelCase(str) {
  return str.replace(/[-_./ ](\w)/g, (_, c) => c.toUpperCase()).replace(/^[A-Z]/, c => c.toLowerCase());
}

function schemaTypeToTs(type, schema, defs, visited = new Set()) {
  if (!schema) return 'unknown';

  if (schema.$ref) {
    const refName = schema.$ref.replace('#/$defs/', '');
    return toPascalCase(refName);
  }

  if (schema.oneOf) {
    return schema.oneOf.map(s => schemaTypeToTs('', s, defs, visited)).join(' | ');
  }

  if (schema.allOf) {
    return schema.allOf.map(s => schemaTypeToTs('', s, defs, visited)).join(' & ');
  }

  if (schema.anyOf) {
    return schema.anyOf.map(s => schemaTypeToTs('', s, defs, visited)).join(' | ');
  }

  if (schema.type === 'array') {
    const itemType = schemaTypeToTs('', schema.items, defs, visited);
    return `z.array(${itemType})`;
  }

  if (schema.type === 'object') {
    if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
      const valueType = schemaTypeToTs('', schema.additionalProperties, defs, visited);
      return `z.record(${valueType})`;
    }
    return 'z.object({...PROPERTIES...})';  // Placeholder — handled in full walk
  }

  if (schema.type === 'string') {
    if (schema.enum) {
      return `z.enum([${schema.enum.map(e => JSON.stringify(e)).join(', ')}])`;
    }
    if (schema.pattern) {
      return `z.string().regex(${JSON.stringify(schema.pattern)})`;
    }
    if (schema.format === 'uri' || schema.format === 'uri-reference') {
      return 'z.string().url()';
    }
    if (schema.format === 'date-time') {
      return 'z.string().datetime()';
    }
    return 'z.string()';
  }

  if (schema.type === 'integer') {
    let s = 'z.number().int()';
    if (schema.minimum !== undefined) s += `.min(${schema.minimum})`;
    if (schema.maximum !== undefined) s += `.max(${schema.maximum})`;
    return s;
  }

  if (schema.type === 'number') {
    let s = 'z.number()';
    if (schema.minimum !== undefined) s += `.min(${schema.minimum})`;
    if (schema.maximum !== undefined) s += `.max(${schema.maximum})`;
    return s;
  }

  if (schema.type === 'boolean') {
    return 'z.boolean()';
  }

  return 'z.any()';
}

function generateTsForDef(defName, def, defs, generated = new Set()) {
  if (generated.has(defName)) return '';
  generated.add(defName);

  const tsName = toPascalCase(defName);
  let lines = [];

  if (!def.type && def.oneOf) {
    // Discriminated union from oneOf
    const variants = def.oneOf.map((v, i) => {
      if (v.$ref) return toPascalCase(v.$ref.replace('#/$defs/', ''));
      if (v.properties?.type?.enum?.[0]) {
        return `z.object({\n  type: z.literal(${JSON.stringify(v.properties.type.enum[0])}),\n  ...\n})`;
      }
      return 'z.any()';
    });
    lines.push(`export const ${tsName}Schema = z.discriminatedUnion('type', [`);
    for (const v of def.oneOf) {
      if (v.$ref) {
        const refName = toPascalCase(v.$ref.replace('#/$defs/', ''));
        lines.push(`  ${refName}Schema,`);
      }
    }
    lines.push(']);');
    lines.push(`export type ${tsName} = z.infer<typeof ${tsName}Schema>;\n`);
    return lines.join('\n');
  }

  if (def.type === 'object' || def.properties) {
    const props = def.properties || {};
    const required = new Set(def.required || []);

    lines.push(`export const ${tsName}Schema = z.object({`);
    for (const [key, prop] of Object.entries(props)) {
      const isReq = required.has(key);
      let tsType = schemaTypeToTs(key, prop, defs, generated);
      // For inline objects, generate proper definition
      if (prop.type === 'object' && prop.properties) {
        const inlineName = `${tsName}_${toPascalCase(key)}`;
        const inlineDef = generateTsForDef(inlineName, prop, defs, generated);
        if (inlineDef) {
          tsType = `${inlineName}Schema`;
        }
      }
      const reqStr = isReq ? '' : '.optional()';
      // Remove trailing .optional() if already there from schema
      lines.push(`  ${key}: ${tsType}${reqStr},`);
    }
    lines.push('});');
    lines.push(`export type ${tsName} = z.infer<typeof ${tsName}Schema>;\n`);
  }

  if (def.type === 'string' && def.enum) {
    lines.push(`export const ${tsName}Schema = z.enum([${def.enum.map(e => JSON.stringify(e)).join(', ')}]);`);
    lines.push(`export type ${tsName} = z.infer<typeof ${tsName}Schema>;\n`);
  }

  return lines.join('\n');
}

function generateAllTs(schemas) {
  const lines = [
    '// Auto-generated from JSON Schema — do not edit manually.',
    '// Run `pnpm gen` from schemas/ to regenerate.',
    '',
    "import { z } from 'zod';",
    '',
  ];

  const allDefs = {};

  // Collect all $defs from all schemas
  for (const s of schemas) {
    if (s.content.$defs) {
      Object.assign(allDefs, s.content.$defs);
    }
  }

  // Generate shared types first
  const generated = new Set();
  for (const [defName, def] of Object.entries(allDefs)) {
    const code = generateTsForDef(defName, def, allDefs, generated);
    if (code) lines.push(code);
  }

  // Generate top-level schema types
  for (const s of schemas) {
    if (s.dir === 'ts') continue; // Skip shared types dir
    const schemaName = s.content.title ? toPascalCase(s.content.title) : toPascalCase(s.name);
    lines.push(generateTsForDef(schemaName, s.content, allDefs, generated));
  }

  const output = lines.join('\n');
  writeFileSync(join(OUT_TS, 'index.gen.ts'), output, 'utf-8');
  console.log(`Generated TS types → ${join(OUT_TS, 'index.gen.ts')}`);
}

function generatePythonForSchema(name, schema, defs) {
  const lines = [
    '# Auto-generated from JSON Schema — do not edit manually.',
    '# Run `pnpm gen` from schemas/ to regenerate.',
    '',
    'from __future__ import annotations',
    'from datetime import datetime',
    'from enum import Enum',
    'from pydantic import BaseModel, Field, field_validator',
    'from typing import Any, Optional',
    'import re',
    '',
  ];

  const seen = new Set();

  function pyTypeFor(prop, propName = '') {
    if (!prop) return 'Any';

    if (prop.$ref) {
      const refName = prop.$ref.replace('#/$defs/', '');
      return toPascalCase(refName);
    }

    if (prop.oneOf) {
      return `Union[${prop.oneOf.map(s => pyTypeFor(s)).join(', ')}]`;
    }

    if (prop.type === 'array') {
      const itemType = pyTypeFor(prop.items || { type: 'any' });
      return `list[${itemType}]`;
    }

    if (prop.type === 'object') {
      if (prop.additionalProperties && typeof prop.additionalProperties === 'object') {
        return `dict[str, ${pyTypeFor(prop.additionalProperties)}]`;
      }
      return 'dict[str, Any]';  // Simplified for codegen
    }

    if (prop.type === 'string') {
      if (prop.enum) {
        const enumName = toPascalCase(propName || 'unknown');
        return enumName;
      }
      return 'str';
    }

    if (prop.type === 'integer') return 'int';
    if (prop.type === 'number') return 'float';
    if (prop.type === 'boolean') return 'bool';

    return 'Any';
  }

  function generateEnum(enumName, values) {
    if (seen.has(enumName)) return '';
    seen.add(enumName);
    return `\nclass ${enumName}(str, Enum):\n${values.map(v => `    ${String(v).toUpperCase().replace(/[^A-Z0-9_]/g, '_')} = ${JSON.stringify(v)}`).join('\n')}\n`;
  }

  function generateModel(modelName, props, required = []) {
    if (seen.has(modelName)) return '';
    seen.add(modelName);

    const requiredSet = new Set(required);
    const lines = [`\nclass ${modelName}(BaseModel):`];
    const modelFields = [];

    for (const [key, prop] of Object.entries(props || {})) {
      let pyType = pyTypeFor(prop, key);

      // Generate enums inline for strings
      if (prop.type === 'string' && prop.enum) {
        const enumName = toPascalCase(key);
        lines.unshift(generateEnum(enumName, prop.enum));
        pyType = enumName;
      }

      const isReq = requiredSet.has(key);
      const defaultStr = isReq ? '' : ' = None';
      const typeStr = isReq ? pyType : `Optional[${pyType}]`;
      modelFields.push(`    ${key}: ${typeStr}${defaultStr}`);
    }

    lines.push(...modelFields);

    // Add field validators for patterns
    for (const [key, prop] of Object.entries(props || {})) {
      if (prop.pattern && prop.type === 'string') {
        const validatorName = `validate_${key}`;
        lines.push('');
        lines.push(`    @field_validator('${key}')`);
        lines.push(`    @classmethod`);
        lines.push(`    def ${validatorName}(cls, v: str) -> str:`);
        lines.push(`        if v is not None and not re.match(${JSON.stringify(prop.pattern)}, v):`);
        lines.push(`            raise ValueError(f"${key} does not match pattern ${prop.pattern}")`);
        lines.push(`        return v`);
      }
    }

    lines.push('');
    return lines.join('\n');
  }

  // Generate all $defs
  for (const [defName, def] of Object.entries(defs || {})) {
    const clsName = toPascalCase(defName);
    if (def.properties) {
      lines.push(generateModel(clsName, def.properties, def.required));
    } else if (def.type === 'string' && def.enum) {
      lines.push(generateEnum(clsName, def.enum));
    } else if (def.oneOf) {
      lines.push(`\n${clsName} = Union[${def.oneOf.map(s => pyTypeFor(s)).join(', ')}]\n`);
    }
  }

  // Generate top-level model
  const topName = toPascalCase(name);
  if (schema.properties) {
    lines.push(generateModel(topName, schema.properties, schema.required));
  }

  return lines.join('\n');
}

function generateAllPy(schemas) {
  const lines = [
    '# Auto-generated from JSON Schema — do not edit manually.',
    '# Run `pnpm gen` from schemas/ to regenerate.',
    '',
    'from __future__ import annotations',
    'from datetime import datetime',
    'from enum import Enum',
    'from pydantic import BaseModel, Field, field_validator',
    'from typing import Any, Optional, Union',
    'import re',
    '',
  ];

  const allDefs = {};
  for (const s of schemas) {
    if (s.content.$defs) {
      Object.assign(allDefs, s.content.$defs);
    }
  }

  // Generate shared types
  const seen = new Set();
  function pyTypeFor(prop) {
    if (!prop) return 'Any';
    if (prop.$ref) {
      return toPascalCase(prop.$ref.replace('#/$defs/', ''));
    }
    if (prop.oneOf) return `Union[${prop.oneOf.map(s => pyTypeFor(s)).join(', ')}]`;
    if (prop.type === 'array') return `list[${pyTypeFor(prop.items || {})}]`;
    if (prop.type === 'object') return 'dict';
    if (prop.type === 'string' && prop.enum) return toPascalCase(prop.type);
    if (prop.type === 'string') return 'str';
    if (prop.type === 'integer') return 'int';
    if (prop.type === 'number') return 'float';
    if (prop.type === 'boolean') return 'bool';
    return 'Any';
  }

  function generateEnum(enumName, values) {
    if (seen.has(enumName)) return '';
    seen.add(enumName);
    return `\nclass ${enumName}(str, Enum):\n${values.map(v => `    ${String(v).toUpperCase().replace(/[^A-Z0-9_]/g, '_')} = ${JSON.stringify(v)}`).join('\n')}\n`;
  }

  function generateModel(modelName, props, required = []) {
    if (seen.has(modelName)) return '';
    seen.add(modelName);
    const requiredSet = new Set(required);
    const fieldLines = [`\nclass ${modelName}(BaseModel):`];
    const validators = [];

    for (const [key, prop] of Object.entries(props || {})) {
      const isReq = requiredSet.has(key);
      let pyType = pyTypeFor(prop);

      if (prop.type === 'string' && prop.enum) {
        const enumName = toPascalCase(key);
        fieldLines.unshift(generateEnum(enumName, prop.enum));
        pyType = enumName;
      }

      const defaultStr = isReq ? '' : ' = None';
      const typeStr = isReq ? pyType : `Optional[${pyType}]`;
      fieldLines.push(`    ${key}: ${typeStr}${defaultStr}`);

      if (prop.pattern && prop.type === 'string') {
        validators.push(`\n    @field_validator('${key}')\n    @classmethod\n    def validate_${key}(cls, v: str) -> str:\n        if v is not None and not re.match(${JSON.stringify(prop.pattern)}, v):\n            raise ValueError(f"${key} does not match required pattern")\n        return v`);
      }
    }

    fieldLines.push(...validators, '');
    return fieldLines.join('\n');
  }

  for (const [defName, def] of Object.entries(allDefs)) {
    const clsName = toPascalCase(defName);
    if (def.properties) {
      lines.push(generateModel(clsName, def.properties, def.required));
    } else if (def.type === 'string' && def.enum) {
      lines.push(generateEnum(clsName, def.enum));
    }
  }

  for (const s of schemas) {
    if (s.dir === 'ts' || s.dir === 'py') continue;
    const modelName = s.content.title ? toPascalCase(s.content.title) : toPascalCase(s.name);
    if (s.content.properties) {
      lines.push(generateModel(modelName, s.content.properties, s.content.required));
    }
  }

  const output = lines.join('\n');
  writeFileSync(join(OUT_PY, 'models.gen.py'), output, 'utf-8');
  console.log(`Generated Python types → ${join(OUT_PY, 'models.gen.py')}`);
}

// Main
const schemas = collectSchemas();
console.log(`Found ${schemas.length} schema files`);

generateAllTs(schemas);
generateAllPy(schemas);

console.log('Codegen complete.');
