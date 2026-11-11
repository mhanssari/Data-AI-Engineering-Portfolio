# Power BI reconciliation (acceptance A10)

Recorded 11 November 2026 on Windows 11: Power BI Desktop 2.158.1177.0 (Microsoft Store), ADOMD.NET 19.84.1, PowerShell 7. Exports from `loyalty run-all` in the Linux container, seed 2602, as-of date 30 September 2026.

## Commands

```powershell
docker compose run --rm loyalty run-all
docker compose run --rm loyalty reference
Start-Process .\bi\RetailLoyalty.pbip
pwsh .\bi\reconcile.ps1
```

## Result

Refreshed from `exports/` in 21 s, then each check's DAX was evaluated by Desktop's engine, under the stated role where there is one.

| Check | Role | Power BI (DAX) | Reference (SQL) | Result |
| --- | --- | --- | --- | --- |
| Net sales, all |  | 1783339.78 | 1783339.78 | match |
| Net sales, August 2026, by transaction date |  | 96516.7 | 96516.7 | match |
| Net sales, August 2026, attributed to sale date |  | 96185.61 | 96185.61 | match |
| Returns, all |  | -45998.94 | -45998.94 | match |
| Member share of net sales |  | 0.512021 | 0.512021 | match |
| Sale transactions |  | 51425 | 51425 | match |
| Average basket |  | 34.6785 | 34.67846 | match |
| Average basket, members |  | 34.9328 | 34.932788 | match |
| Average basket, anonymous |  | 34.4156 | 34.415551 | match |
| Trading days, S012, June 2025 (opened 16 June) |  | 15 | 15 | match |
| Net sales per trading day, S012, June 2025 |  | 90.906 | 90.906 | match |
| Trading days, S007 while in the North (since 1 January 2026) |  | 273 | 273 | match |
| Eligible members, September 2026 |  | 5259 | 5259 | match |
| Eligible members, whole year 2026 (snapshot: last month, not a sum) |  | 5259 | 5259 | match |
| Inactive members, September 2026 |  | 3893 | 3893 | match |
| Inactivity rate, September 2026 |  | 0.740255 | 0.740255 | match |
| Active members, September 2026 |  | 1890 | 1890 | match |
| Redemption rate, all |  | 0.322542 | 0.322542 | match |
| Redemption rate, birthday offers |  | 0.088681 | 0.088681 | match |
| Open entitlements |  | 919 | 919 | match |
| Status conflicts |  | 2 | 2 | match |
| Redemptions at S001 |  | 422 | 422 | match |
| Repeat purchase rate, July 2026 cohort (mature) |  | 0.613861 | 0.613861 | match |
| Repeat purchase rate, August 2026 cohort (immature: blank) |  | blank | blank | match |
| Repeat purchase rate, 2025 cohorts |  | 0.629077 | 0.629077 | match |
| Repeat purchase rate, all cohorts (some immature: blank) |  | blank | blank | match |
| Retention rate, January 2025 cohort, month 6 |  | 0.457143 | 0.457143 | match |
| Tests in the last build |  | 85 | 85 | match |
| Failed tests in the last build |  | 0 | 0 | match |
| As-of date |  | 2026-09-30 | 2026-09-30 | match |
| RLS allowed: Region North sees North net sales | Region North | 467438.16 | 467438.16 | match |
| RLS denied: Region North sees nothing of S001 (London) | Region North | blank | blank | match |
| RLS allowed: Region London sees London sale transactions | Region London | 9666 | 9666 | match |
| RLS scope: programme-wide redemption rate is not filtered by region | Region North | 0.322542 | 0.322542 | match |

34 of 34 checks match. Average baskets differ beyond the fourth decimal because Power BI's fixed-decimal type keeps four places; the comparison allows half a penny.

## Problems found on the way

- **A defect in a measure.** The first run matched 33 of 34. `[Failed Tests in Last Build]` returned blank when no test failed, so a data-health tile would have shown "(Blank)" instead of 0. The measure now adds 0.
- **Two TMDL errors, reported by Desktop when it opened the hand-written project.** A quoted format string on true/false columns was invalid, and relationships cannot carry descriptions.
- **The export folder.** It cannot be relative in Power Query. The script overrides the parameter for its refresh only (a TMSL refresh override), so no machine path is written into the project.
- **Two Desktop windows.** Force-closing one Power BI Desktop while another was open broke the second one's Power Query session ("Could not find a PackageSession"). The script now uses the engine that holds this model, and the README says to keep one Desktop open.

## Not validated

RLS in the Power BI service, where roles are assigned to users; scheduled refresh; any report visual (no pages built yet); performance.
