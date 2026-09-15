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
#    a third-party plugin.
#
# Paths are listed explicitly rather than relying on testpaths so collection
# can never wander outside this repo.
#
# E0.5: this header used to hardcode a measured count/time ("338 collected,
# 13.8s") as if it were a standing guarantee. It went stale the moment more
# tests were added and nobody noticed, because nothing re-checked it -- the
# exact L16 failure mode. Below, `--durations=10` (default via pyproject.toml)
# and the wall-clock line print what THIS run actually measured, every time.
#
# Usage:  ./scripts/test.ps1              (default suite, external tests excluded)
#         ./scripts/test.ps1 -k cache     (extra args pass through to pytest)
#         ./scripts/test.ps1 -m external  (only the excluded external tests)

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

New-Item -ItemType Directory -Force -Path ".publisher" | Out-Null
$Out = ".publisher/test-output.txt"

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"

$started = Get-Date
python -m pytest platform services packages stages tests @args 2>&1 | Tee-Object -FilePath $Out
$code = $LASTEXITCODE
$elapsed = (Get-Date) - $started

Write-Output ""
Write-Output "--- wall time: $([math]::Round($elapsed.TotalSeconds, 1))s ---"
Write-Output "--- full output: $Out ---"
exit $code
