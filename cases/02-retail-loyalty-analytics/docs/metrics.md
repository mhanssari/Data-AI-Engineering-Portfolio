# Metric catalogue

Status: definitions agreed, 11 November 2026; not yet implemented. This file is the one authoritative definition of each metric. SQL and DAX link here instead of repeating it.

The **as-of date** is the latest transaction date loaded unless overridden. "Window closed" always means the window ends on or before the as-of date.

## Net sales

| | |
| --- | --- |
| Question | How much did we sell after returns? |
| Owner | Finance |
| Formula | Sum of `net_amount` on `fct_sales_line`: sale lines positive, return lines negative |
| Unit | GBP, two decimals |
| Time basis | **Transaction date** (finance view: a return reduces the period it happens in), or **attributed sale date** (cohort and product views: a return reduces the period of the original sale) |
| Filters | None by default; member and anonymous sales both included |
| Exclusions | None. Unmatched returns are attributed to their return date on both bases |
| Missing data | A line with an unknown product still counts, under an inferred product |
| Example | Sale of £50 on 3 March, £20 returned on 10 April: March £50 and April −£20 by transaction date; March £30 and April £0 by attributed date. Both total £30 |

## Net sales per trading day

| | |
| --- | --- |
| Question | Which stores perform well, comparing stores that opened at different times? |
| Owner | Store operations |
| Formula | Net sales by transaction date ÷ days the store was open in the period |
| Unit | GBP per day |
| Time basis | Transaction date |
| Exclusions | Days before a store opened |
| Zero denominator | Blank, not zero |
| Example | A store opened on 16 June with £30,000 in June: £30,000 ÷ 15 days = £2,000 per day |

*Added during design so that store comparisons are fair; flagged for review.*

## Active member and inactivity rate

| | |
| --- | --- |
| Question | Which members have stopped buying? |
| Owner | Loyalty marketing |
| Qualifying purchase | A sale transaction linked to the member whose net amount, after returns attributed to it, is above zero. A fully returned purchase does not count |
| Active member | At least one qualifying purchase in the 90 days ending on the measurement date |
| Eligible member | Enrolled at least 90 days before the measurement date, so the full window is observable. **Members who never bought are eligible** |
| Inactivity rate | Eligible members who are not active ÷ eligible members |
| Grain | Member per month, measured at month end (`fct_member_month`) |
| Zero denominator | Blank |
| Example | Enrolled 1 January, never bought: at 31 March (89 days) not yet eligible; at 30 April eligible and inactive |

## Repeat-purchase rate

| | |
| --- | --- |
| Question | How many new members come back? |
| Owner | Loyalty marketing |
| Cohort | Members grouped by the month of their first qualifying purchase on or after enrolment |
| Numerator | Cohort members with a second qualifying purchase on a later date, within 60 days of the first |
| Denominator | Cohort members whose 60-day window has closed |
| Maturity | A cohort is mature when every member's window has closed. Immature cohorts are reported with a null rate and labelled, never as 0% |
| Exclusions | Members with no qualifying purchase (they appear in inactivity instead); a second transaction on the same day as the first |
| Example | First purchase 20 August, as-of date 30 September: the window closes 19 October, so the August cohort is immature |

## Retention by enrolment cohort

| | |
| --- | --- |
| Question | How does activity decay after joining? |
| Owner | Loyalty marketing |
| Formula | For enrolment month *c* and month offset *k*: members of *c* active at the end of month *c + k* ÷ members of *c* |
| Completeness | A cell exists only when the end of month *c + k* is on or before the as-of date |
| Note | The activity window is 90 days, so offsets 0 to 2 measure early activity, not retention. The report starts the curve at offset 3 and says why |

## Redemption rate

| | |
| --- | --- |
| Question | Which reward types are used? |
| Owner | Loyalty marketing |
| Formula | Entitlements redeemed ÷ entitlements resolved (redeemed or expired) |
| Grain | Entitlement (`fct_reward_entitlement`) |
| Time basis | Issue month |
| Exclusions | Open entitlements (not yet expired, not redeemed) and cancelled ones, so a young reward never reads as unused |
| Status source | The event log wins over the status column when they disagree; disagreements are counted |
| Zero denominator | Blank |
| Example | 100 issued in May: 40 redeemed, 35 expired, 20 open, 5 cancelled. Rate = 40 ÷ 75 = 53.3% |

## Member share of sales and basket value

| | |
| --- | --- |
| Question | How do member and non-member purchases differ? |
| Owner | Loyalty marketing, read by finance |
| Formulas | Member share = member net sales ÷ net sales. Average basket = net sales ÷ sale transactions, split by member and anonymous |
| Caveat | **Descriptive only.** Members differ from non-members in ways the data does not record; the difference is not an effect of the programme |
