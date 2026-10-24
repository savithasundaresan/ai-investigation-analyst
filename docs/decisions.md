# Analytical decisions log

Each entry records what we decided, why, and what we gave up. Use it to answer
"why did you do it this way?" in interviews. Numbers come from profiling the
real data in Stage 1; the queries behind them are in
[data_profile.md](data_profile.md).

## D1. One row per order is the unit of analysis
Three child tables can have more than one row per order:

| Table | Grain | Repeats in the real data |
|---|---|---|
| `orders` | 1 row per order | 99,441 orders |
| `order_items` | 1 row per item | 9,803 orders have more than one item |
| `order_payments` | 1 row per payment | 2,961 orders have more than one payment row |
| `order_reviews` | 1 row per review | 547 orders have more than one review |

Joining these at their native grain multiplies rows. Joining payments to
items directly reports **R$ 20.31M** of payments when the true total is
**R$ 15.85M**: 28% too high, and it still looks plausible.
**Rule:** the `int_` layer collapses each child table to one row per order
*first*; marts join only those. Tests check that order counts, item value and
payment totals match the source exactly.

## D2. Late delivery definition
- **Late** = delivered on a later *calendar day* than the estimated delivery
  date. The estimate has no time of day, so delivery at 15:00 on the
  estimated day is on time.
- **Delivered** = status `delivered` **and** a delivery date. This excludes 8
  "delivered" orders with no date and 6 canceled orders that have a date.
- **Denominator** = delivered orders. Canceled orders are a separate metric.
- **Overdue and undelivered** (1,729 orders): orders never delivered, not
  canceled, with an estimate that passed before the data ends. They can't be
  in the late rate because they have no delivery date, but ignoring them
  would understate the problem. So they are reported next to the late rate
  as their own metric.
- *Trade-off:* the late rate counts how often orders are late, not how late.
  Average days late is reported alongside it.

## D3. Orders belong to their purchase month; only complete months are analyzed
We assumed recent months would look better than they really were, because
their late orders hadn't been delivered yet (right-censoring). The data shows
the effect is small here: in every month from 2017 through August 2018, at
most 5% of orders are still in an open status.
**Rule (`dim_month.is_complete`):** a month is analyzed only if it has
≥ 500 orders, ≤ 5% of orders still open, and ≤ 1% of estimated delivery
dates after the last date in the data (2018-10-17).
**Result:** the analysis window is **2017-01 to 2018-08**, 20 complete months.
Excluded: Sept–Dec 2016 (329 orders in total) and Sept–Oct 2018 (20 orders).
*Caveat:* average delivery time drops steadily through mid-2018 (to 7.7 days
in August). The data can't tell us whether that's real improvement or slow
orders missing from the export, so it's listed as a limitation.

## D4. Seller and category breakdowns use order × seller × category rows
1,278 orders (1.3% of orders with items) include more than one seller.
`bridge_order_segment` has one row per order × seller × category. Breakdowns
count `DISTINCT order_id` per segment, so a split order counts once for each
seller involved and never twice for the same one. Giving the whole order to
one "main" seller would hide a slow seller behind a fast one.

## D5. Separate the change in order mix from the change in performance (key differentiator)
```
Δ overall = Σ (Δshare_i × baseline_rate_i)     ← mix: more orders went to slower segments
          + Σ (baseline_share_i × Δrate_i)     ← performance: segments themselves got slower
          + Σ (Δshare_i × Δrate_i)             ← interaction
```
This is a standard shift-share decomposition. The three parts add up exactly
to the total change. If mix dominates, the cause is where volume went, not
seller performance, and the two call for different actions. Built in Stage 2.

## D6. Anomaly rules are simple and visible
A segment is flagged only if it has at least **30 delivered orders in both
periods**, its late-rate change has **|z| ≥ 2** (two-proportion z-test), and
the change is at least **3 percentage points**. Each flag shows these numbers.
The thresholds are tuned in Stage 2.

## D7. "This month" is a replay of historical data
The user picks a month from the analysis window to treat as "current". The
app labels it **Simulation: historical replay**.

## D8. The LLM chooses what to investigate; SQL computes every number
The agent can only call read-only, parameterized tools. Every number in a
report must come from a logged tool result. The LLM never does arithmetic.

## D9. Free local model by default; the LLM layer can be swapped
The agent uses a local model through **Ollama** (default `qwen3:8b`). All model
calls go through `src/llm.py`, and `LLM_PROVIDER` in `.env` picks the backend;
Claude is an optional extra.
- *Why:* it costs nothing, data stays on the machine, and anyone can run it.
- *Trade-off:* small local models pick the wrong tool more often and are
  slower. The D8 safeguards and the Stage 4 evaluation make that visible.
- *Replay mode:* saved runs can be played back with no model running, so the
  demo works for anyone who clones the repo.

## D10. Order value = item prices + freight, not payments
For 249 orders, the payment total differs from the item total by more than
R$ 1, mostly from instalment interest and vouchers. We use **price + freight**
because it can be split per seller and per category (payments are per order
only). The payment total is kept as a column, and mismatched orders are
flagged (`dq_payment_mismatch`).

## D11. Data source: pinned public mirror, verified by checksum
The Kaggle files come from a public Hugging Face mirror, pinned to one commit.
Each file's SHA-256 is checked on every load. The checksums matched two other
independent mirrors and the published Git LFS checksums, and the row counts
match the published Olist dataset (e.g. 99,441 orders). The raw layer stores
every column as text, so it's an exact copy and all typing happens in
visible SQL. The 61 MB geolocation file is not loaded because regions use
state codes. Licence: CC BY-NC-SA 4.0 (non-commercial use; credit Olist).

## D12. Data-quality problems are flagged, not deleted
166 orders were handed to the carrier *before* they were purchased, and
23 were delivered *before* reaching the carrier. 775 orders have no items
(mostly `unavailable` or `canceled`), and 1 has no payment. These rows keep
a `dq_` flag rather than being dropped silently. None of them affects the
late flag, which uses only the delivery and estimated dates, and
`check_data_quality` reports them for any period.
