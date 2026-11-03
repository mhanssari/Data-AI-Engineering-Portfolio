# Data samples

`expected_outcomes.csv`: the expected result of every planted scenario, written by the generator and asserted by the tests. Columns: scenario, description, company, source system, document type and number, line, expected status, expected reason codes (`|` separated; for supplier pairs, the expected method), phase (`batch_001`, `final` or `all`) and detail. See [scenarios](../../docs/scenarios.md).
