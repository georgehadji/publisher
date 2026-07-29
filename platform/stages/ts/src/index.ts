/**
 * Publisher Stage Registry
 *
 * Every stage registers itself via the @stage decorator.
 * From this declaration five things are derived (ARCHITECTURE.md §2.8.1):
 *   - The DAG (match inputs/outputs between stages)
 *   - Contract tests (run over fixture sets, assert schema validation)
 *   - Cache key inputs (name + version + input hashes + params + toolchain)
 *   - Local dev harness
 *   - Admission control (memory budget, queue)
 */

/** Error taxonomy from ARCHITECTURE.md §2.8 */
export enum ErrorKind {
  BadInput = "bad_input",
  PolicyViolation = "policy_violation",
  EngineBug = "engine_bug",
  Infra = "infra",
  ExternalLimit = "external_limit",
}

/** Structured diagnostic message. */
export interface Diagnostic {
  code: string;
  severity: "error" | "warning" | "info";
  humanMessage: string;
  suggestedFix?: string;
  sourceRef?: string;
}

/** Error thrown by a stage. */
export class StageError extends Error {
  constructor(
    public readonly kind: ErrorKind,
    message: string,
    public readonly diagnostics: Diagnostic[] = [],
    public readonly retryCount = 0,
    public readonly retryable = false
  ) {
    super(message);
    this.name = "StageError";

    if (retryable && kind !== ErrorKind.Infra && kind !== ErrorKind.ExternalLimit) {
      throw new Error(
        `Only infra and external_limit errors are retryable, got ${kind}`
      );
    }
  }
}

/** Reference to an artifact in the content-addressed store. */
export interface ArtifactRef {
  kind: string;
  hash: string; // sha256 hex
  mediaType: string;
  size: number;
}

/** Result produced by a stage execution. */
export interface StageResult {
  artifacts: ArtifactRef[];
  metrics?: Record<string, number>;
  warnings?: Diagnostic[];
  toolchain?: Record<string, unknown>;
}

/** Context provided to a stage when it runs. */
export interface StageCtx {
  buildId: string;
  cacheKey: string;
  deadline: Date;
  memoryBudgetMb: number;
  workDir: string;
}

/** Stage function signature. */
export type StageFn = (ctx: StageCtx, inputs: Record<string, unknown>) => StageResult | Promise<StageResult>;

/** Immutable declaration of a stage. */
export interface StageDeclaration {
  name: string;
  version: number;
  inputs: Record<string, string>; // param_name → schema_id
  outputs: Record<string, string>; // artifact_kind → schema_id
  toolchain: string[]; // names of required toolchain components
  fixtures?: string; // fixture set path or version
  memoryBudgetMb: number;
  queue: string;
  description: string;
}

/**
 * The stage registry.
 * Stages register themselves; the registry derives DAG, tests, and cache keys.
 */
export class StageRegistry {
  private _stages = new Map<string, StageDeclaration & { fn: StageFn }>();

  register(name: string, decl: StageDeclaration, fn: StageFn): void {
    if (this._stages.has(name)) {
      throw new Error(`Stage '${name}' is already registered`);
    }
    this._stages.set(name, { ...decl, fn });
  }

  get(name: string): (StageDeclaration & { fn: StageFn }) | undefined {
    return this._stages.get(name);
  }

  all(): (StageDeclaration & { fn: StageFn })[] {
    return Array.from(this._stages.values());
  }

  names(): string[] {
    return Array.from(this._stages.keys()).sort();
  }

  /**
   * Derive the DAG by matching each stage's declared inputs
   * to other stages' declared outputs.
   * Returns adjacency list: stage_name → [dependency_stage_names].
   */
  deriveDag(): Record<string, string[]> {
    // Build reverse map: schema_id → [stage_names_that_produce_it]
    const producers: Record<string, string[]> = {};
    for (const [name, decl] of this._stages) {
      for (const schemaId of Object.values(decl.outputs)) {
        (producers[schemaId] ??= []).push(name);
      }
    }

    const dag: Record<string, string[]> = {};
    for (const [name, decl] of this._stages) {
      const deps = new Set<string>();
      for (const schemaId of Object.values(decl.inputs)) {
        const prod = producers[schemaId];
        if (prod) {
          for (const d of prod) deps.add(d);
        }
      }
      dag[name] = Array.from(deps).sort();
    }

    return dag;
  }

  /**
   * Return stages in topological (execution) order.
   */
  topologicalSort(): string[] {
    const dag = this.deriveDag();
    const visited = new Set<string>();
    const result: string[] = [];

    const visit = (node: string) => {
      if (visited.has(node)) return;
      visited.add(node);
      for (const dep of dag[node] ?? []) {
        visit(dep);
      }
      if (this._stages.has(node)) {
        result.push(node);
      }
    };

    for (const name of this.names()) {
      visit(name);
    }

    return result;
  }
}

// Global singleton
const GLOBAL_REGISTRY = new StageRegistry();

export function getRegistry(): StageRegistry {
  return GLOBAL_REGISTRY;
}

/**
 * Decorator-esque helper to register a stage.
 *
 * Usage:
 * ```ts
 * registerStage({
 *   name: "finish",
 *   version: 9,
 *   inputs: { pdf: "raw-pdf/1", profile: "profile/1" },
 *   outputs: { pdf: "pdfx/1", report: "finish-report/1" },
 *   toolchain: ["ghostscript", "icc"],
 *   fixtures: "fixtures/finish/v3",
 *   memoryBudgetMb: 512,
 *   queue: "q.prepress",
 *   description: "Apply CMYK conversion, bleed, marks, OutputIntent",
 * }, async (ctx, inputs) => {
 *   // ... stage logic
 *   return { artifacts: [], metrics: {} };
 * });
 * ```
 */
export function registerStage(decl: StageDeclaration, fn: StageFn): void {
  getRegistry().register(decl.name, decl, fn);
}

/**
 * Execute a registered stage by name.
 */
export async function runStage(
  name: string,
  ctx: StageCtx,
  ...args: unknown[]
): Promise<StageResult> {
  const decl = getRegistry().get(name);
  if (!decl) {
    throw new Error(
      `Unknown stage: '${name}'. Registered stages: ${getRegistry().names().join(", ")}`
    );
  }
  return decl.fn(ctx, args);
}
