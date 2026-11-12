# Data sources and fixtures

All data is synthetic, produced by the seeded generator in `src/payrecon/generator.py` (seed 3303 in `src/payrecon/config.py`). Nothing is bundled: `payrecon generate` reproduces the same files byte for byte.

## What is generated

One folder per arrival day, `data/generated/arrival_<YYYY-MM-DD>/`, from 2 October to 1 November 2026. Arrival day A holds:

| File | Grain | Notes |
|---|---|---|
| `manifest.json` | One entry per file | File name, kind, row count, SHA-256 |
| `merchants.csv` (first arrival) and `merchants_<day>.csv` | Merchant | 40 merchants at the start; M041 sent late (P5) |
| `events_<A-1>.csv` | Delivered payment event | Events ingested on day A − 1, about 5,700 a day |
| `settlement_<CCY>_<A-1>.csv` | Settlement line | One file per currency (GBP, EUR, USD) and settlement day |

Background: about 2,000 payments a day from 1 to 30 October, generated cleanly. Planted defects: P1–P16 in [the data model](../docs/data-model.md), with ids from `pay-9000001`; their expected outcomes are in `src/payrecon/scenarios.py`.

Money is integer minor units with a currency ([decision 0002](../docs/decisions/0002-money-in-minor-units.md)).
