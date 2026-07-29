# Publisher — Golden Corpus

For the build plan and corpus strategy, see [BUILD_PLAN.md §8](docs/BUILD_PLAN.md) and [OPTIMIZATION.md §O8](docs/OPTIMIZATION.md).

## Contents

```
corpus/
  manuscripts/       # Source manuscripts (DOCX + expected ASTs)
  raster-diff/       # Raster-based regression harness
  golden/            # Golden outputs (verified by typographer)
```

## Manuscripts

| ID | Title | Description | Pages | Status |
|---|---|---|---|---|
| mini-novel-v1 | The Test Novel | 3-chapter literary novel | ~12 | fixture |
| short-story-v1 | The Quick Fox | Single-chapter with verse/dialogue | ~5 | fixture |
| ... | (more to come as corpus license is secured) | | | |

## Toolchain

- `raster-diff/compare.py` — Compare a build output against golden rasters
- `raster-diff/harness.py` — Run regression tests across the corpus

## Licensing

Real manuscripts from published works require licensing (Track B, week 1).
See [BUILD_PLAN.md §5.0] for the parallel track and [§8] for the synthetic
corpus hedge (O8) that does not depend on external licensing.
