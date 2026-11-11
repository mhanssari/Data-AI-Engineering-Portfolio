# Data

All synthetic. Nothing here comes from a real retailer, and no data is committed: `uv run loyalty generate` writes both batches to `data/generated/` (ignored by Git) from seed 2602, byte for byte the same on every run.

| | |
| --- | --- |
| Generator | [src/loyalty/generator.py](../src/loyalty/generator.py), Python standard library only |
| Seed | 2602 (`--seed` to change) |
| Period | 1 October 2024 to 30 September 2026; batch 1 to 16 September, batch 2 the rest plus late material |
| Files per batch | Seven CSV extracts and a `manifest.json` with row counts and SHA-256 checksums |
| Planted defects | D1–D15, declared with their expected outcomes in [scenarios.py](../src/loyalty/scenarios.py) and described in the [data model](../docs/data-model.md#planted-defects) |

Sources, grain, keys and measured volumes are in the [data model](../docs/data-model.md#sources).
