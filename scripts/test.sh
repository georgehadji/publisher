#!/usr/bin/env bash
# Publisher test runner (POSIX/CI) -- docs/COST_AND_STABILITY_PLAN.md S1.
# See scripts/test.ps1 for why results are project-local and plugin autoload
# is disabled. Keep the two in sync.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

mkdir -p .publisher
OUT=".publisher/test-output.txt"

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

python -m pytest platform services packages stages tests "$@" 2>&1 | tee "$OUT"
code=${PIPESTATUS[0]}

echo
echo "--- full output: $OUT ---"
exit "$code"
