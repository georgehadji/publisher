# Publisher test runner -- docs/COST_AND_STABILITY_PLAN.md S1.
#
# Use this instead of a bare `pytest`. Two reasons, both learned the hard way:
#
# 1. RESULTS MUST BE PROJECT-LOCAL. Bare pytest output was being read back out
#    of a global, cross-project tee directory shared by every repo on this
#    machine. A concurrent run in a sibling repo was picked up and its failures
#    reported as Publisher's. Results go to .publisher/test-output.txt, which
#    only this repo writes.
#
# 2. PLUGIN AUTOLOAD COSTS ~100s PER RUN. Third-party pytest plugin discovery
#    scans site-packages metadata; on Windows that is subject to real-time AV
#    scanning and `pytest --version` alone took 2m30s. No test here depends on
#    a third-party plugin -- verified: 338 collected, zero errors, 13.8s.
#
# Paths are listed explicitly rather than relying on testpaths so collection
# can never wander outside this repo.
#
# Usage:  ./scripts/test.ps1              (whole suite)
#         ./scripts/test.ps1 -k cache     (extra args pass through to pytest)

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

New-Item -ItemType Directory -Force -Path ".publisher" | Out-Null
$Out = ".publisher/test-output.txt"

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"

python -m pytest platform services packages stages tests @args 2>&1 | Tee-Object -FilePath $Out
$code = $LASTEXITCODE

Write-Output ""
Write-Output "--- full output: $Out ---"
exit $code
