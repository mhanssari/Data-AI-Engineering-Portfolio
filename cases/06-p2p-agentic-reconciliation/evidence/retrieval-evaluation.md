# Retrieval evaluation

**Date:** 2026-11-02. Written by `uv run p2p eval-retrieval`; do not edit by hand.

**Corpus:** 84 synthetic documents, 238 chunks, default seed. **Embedding:** `BAAI/bge-small-en-v1.5`, 384 dimensions, through fastembed 0.9.1, exact scan.

**Labels:** 36 hand-written questions in `evaluation/retrieval_questions.yaml`. This set is small and was written by the author of the corpus, so read differences between methods as indicative only. Latency is one machine and one pass, with the graph on a local container.

| Method | Recall@1 | Recall@3 | Recall@5 | MRR | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| keyword | 39% | 58% | 67% | 0.509 | 2.0 | 6.0 |
| vector | 50% | 69% | 78% | 0.622 | 30.9 | 43.9 |
| hybrid | 44% | 67% | 72% | 0.591 | 34.6 | 45.6 |
| hybrid_graph | 56% | 81% | 83% | 0.705 | 37.9 | 1282.5 |

## By kind of question

| Kind | Questions | Method | Recall@1 | Recall@5 | MRR |
| --- | --- | --- | --- | --- | --- |
| anchored | 8 | keyword | 0% | 50% | 0.214 |
| anchored | 8 | vector | 0% | 50% | 0.240 |
| anchored | 8 | hybrid | 12% | 50% | 0.299 |
| anchored | 8 | hybrid_graph | 62% | 100% | 0.812 |
| email | 4 | keyword | 100% | 100% | 1.000 |
| email | 4 | vector | 0% | 25% | 0.170 |
| email | 4 | hybrid | 50% | 75% | 0.598 |
| email | 4 | hybrid_graph | 50% | 75% | 0.598 |
| procedure | 4 | keyword | 75% | 100% | 0.833 |
| procedure | 4 | vector | 100% | 100% | 1.000 |
| procedure | 4 | hybrid | 100% | 100% | 1.000 |
| procedure | 4 | hybrid_graph | 100% | 100% | 1.000 |
| rule | 12 | keyword | 42% | 67% | 0.528 |
| rule | 12 | vector | 50% | 92% | 0.649 |
| rule | 12 | hybrid | 50% | 67% | 0.609 |
| rule | 12 | hybrid_graph | 50% | 67% | 0.609 |
| supplier | 8 | keyword | 25% | 50% | 0.368 |
| supplier | 8 | vector | 100% | 100% | 1.000 |
| supplier | 8 | hybrid | 38% | 88% | 0.646 |
| supplier | 8 | hybrid_graph | 38% | 88% | 0.646 |

## Rank of the first expected chunk, per question

A dash means the method did not return it at all.

| Question | Kind | keyword | vector | hybrid | hybrid_graph |
| --- | --- | --- | --- | --- | --- |
| How far over the order price can an invoice go before we stop it? | rule | 3 | 20 | 6 | 6 |
| Is a one penny difference treated as a mismatch? | rule | 2 | 1 | 1 | 1 |
| Can somebody sign off their own purchase request? | rule | 3 | 2 | 3 | 3 |
| How long after new bank details do we keep holding a supplier's invoices? | rule | 1 | 1 | 1 | 1 |
| What counts as the same invoice submitted twice? | rule | 25 | 1 | 12 | 12 |
| Several small requests in the same week to one vendor: when is that a problem? | rule | 23 | 5 | 7 | 7 |
| Who may approve a request of 20,000? | rule | 20 | 5 | 12 | 12 |
| An order was raised after the bill arrived. What happens? | rule | 1 | 1 | 1 | 1 |
| A supplier billed us in dollars but ordered in pounds. | rule | 25 | 3 | 2 | 2 |
| Can a supplier's credit be worth more than the bill it refers to? | rule | 1 | 2 | 1 | 1 |
| How is the month-end accrual for undelivered paperwork valued? | rule | 1 | 1 | 1 | 1 |
| We were billed but nobody quoted an order number. | rule | 1 | 1 | 1 | 1 |
| What is the price tolerance for Kelso Glass? | supplier | 4 | 1 | 2 | 2 |
| What is the price tolerance for Otley Labels? | supplier | 8 | 1 | 2 | 2 |
| How much over the agreed amount may Jena Textil invoice? | supplier | 1 | 1 | 1 | 1 |
| When do we have to pay Detmold Nahrung? | supplier | 7 | 1 | 2 | 2 |
| When do we have to pay Hexley Print? | supplier | 8 | 1 | 2 | 2 |
| What price did we agree with Nairn Metals? | supplier | 19 | 1 | 6 | 6 |
| How quickly must Hameln Technik deliver? | supplier | 4 | 1 | 1 | 1 |
| How much notice to end the agreement with Malvern Pallets? | supplier | 1 | 1 | 1 | 1 |
| Who countersigns the call when a supplier changes bank account? | procedure | 3 | 1 | 1 | 1 |
| Who countersigns the call when a supplier changes bank account? | procedure | 1 | 1 | 1 | 1 |
| Which working day do we review the accrual? | procedure | 1 | 1 | 1 | 1 |
| What is internal audit sampling this year? | procedure | 1 | 1 | 1 | 1 |
| A supplier says it will not accept a short payment because it told our buyer about an increase. | email | 1 | 33 | 7 | 7 |
| Which supplier has a signed driver's sheet for the full load? | email | 1 | 2 | 1 | 1 |
| Somebody asked us to pay into a new account and will only reply by email. | email | 1 | 14 | 1 | 1 |
| Work was requested by telephone, with no reference on the bill. | email | 1 | 13 | 4 | 4 |
| What tolerance applies to this invoice under its contract in February? | anchored | 3 | 2 | 1 | 1 |
| What tolerance applies to this invoice under its contract in May? | anchored | 4 | 5 | 3 | 2 |
| What has the supplier said about this invoice? | anchored | 39 | 6 | 18 | 2 |
| What has the supplier said about this invoice? | anchored | 2 | 23 | 6 | 1 |
| What are the payment terms with this supplier? | anchored | 10 | 10 | 12 | 1 |
| What is the delivery commitment from this supplier? | anchored | 8 | 3 | 5 | 1 |
| Has anybody in accounts payable left a note about this invoice? | anchored | 3 | 2 | 2 | 1 |
| What has the supplier said about this invoice? | anchored | 23 | 13 | 19 | 2 |
