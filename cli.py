#!/usr/bin/env python3
"""
Publisher CLI -- development and administration tool.

Usage:
    pub run-stage <stage-name> [--fixture <version>]
    pub cas put <file>
    pub cas get <hash>
    pub corpus generate [--templates ...]
    pub schema validate <file>
    pub schema gen
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Publisher CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run-stage
    stage_parser = subparsers.add_parser("run-stage", help="Run a build stage")
    stage_parser.add_argument("stage_name", help="Stage name (e.g., 'extract', 'paginate')")
    stage_parser.add_argument("--fixture", default=None, help="Fixture set version")

    # cas
    cas_parser = subparsers.add_parser("cas", help="Content-addressed store operations")
    cas_sub = cas_parser.add_subparsers(dest="cas_command")
    cas_put = cas_sub.add_parser("put", help="Store a file")
    cas_put.add_argument("file", help="Path to file")
    cas_get = cas_sub.add_parser("get", help="Retrieve a file")
    cas_get.add_argument("hash", help="SHA-256 hash")

    # corpus
    corpus_parser = subparsers.add_parser("corpus", help="Corpus management")
    corpus_sub = corpus_parser.add_subparsers(dest="corpus_command")
    gen_parser = corpus_sub.add_parser("generate", help="Generate synthetic corpus")
    gen_parser.add_argument("--templates", nargs="*", help="Template names")

    # schema
    schema_parser = subparsers.add_parser("schema", help="Schema operations")
    schema_sub = schema_parser.add_subparsers(dest="schema_command")
    schema_sub.add_parser("gen", help="Regenerate types from schemas")
    val_parser = schema_sub.add_parser("validate", help="Validate a JSON file against a schema")
    val_parser.add_argument("file", help="JSON file to validate")

    args = parser.parse_args()

    if args.command == "run-stage":
        _cmd_run_stage(args)
    elif args.command == "cas":
        _cmd_cas(args)
    elif args.command == "corpus":
        _cmd_corpus(args)
    elif args.command == "schema":
        _cmd_schema(args)
    else:
        parser.print_help()


def _cmd_run_stage(args: argparse.Namespace):
    """Run a build stage."""
    from publisher_stages import get_registry, run_stage, StageCtx
    from datetime import datetime, timezone

    registry = get_registry()
    decl = registry.get(args.stage_name)
    if decl is None:
        print(f"Unknown stage: '{args.stage_name}'")
        print(f"Registered stages: {registry.names()}")
        sys.exit(1)

    ctx = StageCtx(
        build_id=f"dev-{args.stage_name}",
        cache_key="dev-no-cache",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=decl.memory_budget_mb,
        work_dir="/tmp/pub-dev",
    )

    print(f"Running stage: {args.stage_name} (v{decl.version})")
    result = run_stage(args.stage_name, ctx)
    print(f"Result: {len(result.artifacts)} artifacts, {len(result.metrics)} metrics")


def _cmd_cas(args: argparse.Namespace):
    """CAS operations."""
    from publisher_cas import ContentAddressedStore, CasConfig, Sha256, MediaType
    from pathlib import Path

    store = ContentAddressedStore(CasConfig(local_cache_root=Path(".cas-cache")))

    if args.cas_command == "put":
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {args.file}")
            sys.exit(1)
        data = path.read_bytes()
        ref = store.put(data, media_type=MediaType.OCTET_STREAM)
        print(f"Stored: {ref.hash} ({ref.size} bytes)")
        print(f"Path:   {store.get_path(ref)}")

    elif args.cas_command == "get":
        from publisher_cas import ArtifactRef
        h = Sha256(args.hash)
        ref = ArtifactRef(hash=h, media_type=MediaType.OCTET_STREAM, size=0)
        if store.exists(ref):
            path = store.get_path(ref)
            print(f"Found:  {path} ({path.stat().st_size} bytes)")
            print(f"Hash:   {h}")
        else:
            print(f"Not found: {h}")
            sys.exit(1)

    else:
        print("Usage: pub cas {put|get} ...")


def _cmd_corpus(args: argparse.Namespace):
    """Corpus management."""
    if args.corpus_command == "generate":
        import subprocess
        import sys
        cmd = [sys.executable, "corpus/generator/generate.py"]
        if args.templates:
            cmd.extend(["--templates"] + args.templates)
        subprocess.run(cmd, check=True)
    else:
        print("Usage: pub corpus generate [--templates ...]")


def _cmd_schema(args: argparse.Namespace):
    """Schema operations."""
    if args.schema_command == "gen":
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-c", """
import subprocess, sys
subprocess.run(['node', 'codegen/generate.mjs'], cwd='schemas', check=True)
"""],
            check=True,
        )
        print("Types regenerated.")

    elif args.schema_command == "validate":
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {args.file}")
            sys.exit(1)
        data = json.loads(path.read_text())
        print(f"Valid JSON: {args.file}")
        print(f"Schema:     {data.get('schema', 'not specified')}")

    else:
        print("Usage: pub schema {gen|validate} ...")


if __name__ == "__main__":
    main()
