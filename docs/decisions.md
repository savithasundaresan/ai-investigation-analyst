# Analytical decisions log

Each entry records what we decided, why, and what we gave up. Use it to answer
"why did you do it this way?" in interviews. Entries marked **(to verify)** are
working assumptions that Step 1 will confirm or revise against the actual data.

## D1. One row per order is the unit of analysis
Olist stores one order across several tables, and three of them can have more
than one row per order:

| Table | Grain | Why it can repeat |
|---|---|---|
| `orders` | 1 row per order | the base table |
| `order_items` | 1 row per item | an order can contain several items, sometimes from different sellers |
| `order_payments` | 1 row per payment | split payments, vouchers |
| `order_reviews` | 1 row per review | a few orders have more than one review **(to verify)** |

Joining all of these directly multiplies rows. For example, an order with 3 items
and 2 payments becomes 6 rows, so its payment total is counted 3 times
("join fan-out"). **Rule:** aggregate each child table to one row per order
*first*, then join. Tests check that the order count and the payment total
match the source tables after every join.

## D2. Late delivery definition
- **Late** = the order was delivered to the customer *after* the estimated
  delivery date, compared on calendar date. The estimate is stored as a date
  with no time, so a delivery at 15:00 on the estimated day counts as on time.
- **Denominator** = delivered orders that have a delivery date. Canceled or
  undelivered orders are tracked as a separate metric, not counted as late or on time.
- *Trade-off:* this ignores how late an order was. We also report average days
  late so a 1-day miss and a 20-day miss aren't treated as equal.

## D3. Which month an order belongs to: purchase month
We assign orders to the month they were purchased. Problem: for the last months
in the dataset, many orders hadn't been delivered by the time the data was
exported. The late ones are the most likely to still be missing, so those
months would look better than they really were (**right-censoring**).
**Rule:** we exclude months where too large a share of orders lack a final
status. The exact cutoff will be set in Step 1.

## D4. Seller and category breakdowns use order × seller rows
About 1–3% of orders contain items from more than one seller **(to verify)**.
For seller or category breakdowns we count each order once per seller (or
category) it involves, and we disclose the share of affected orders. Giving the
whole order to one "main" seller would hide that one seller's delay.

## D5. Separate the change in order mix from the change in performance (key differentiator)
A change in the overall late rate between two periods splits exactly into three parts:

```
Δ overall = Σ (Δshare_i × baseline_rate_i)     ← mix: more orders went to slower segments
          + Σ (baseline_share_i × Δrate_i)     ← performance: segments themselves got slower
          + Σ (Δshare_i × Δrate_i)             ← interaction
```
This is a standard shift-share decomposition. If the "mix" part dominates, the
answer is "we sent more volume to historically slow sellers or regions", not
"sellers got worse". The two answers call for different actions.

## D6. Anomaly rules are simple and visible
A segment is flagged only if all of these hold:
- it has at least **N delivered orders in both periods** (minimum sample;
  N set in Step 2), and
- its late rate changed by more than the noise threshold (a two-proportion
  z-test, |z| ≥ 2), and
- the change is large enough to matter in practice (absolute change ≥ X
  percentage points).
Every flag shows these numbers, so a viewer can check why the segment was flagged.

## D7. "This month" is a replay of historical data
The data covers roughly 2016–2018. The app lets the user pick a month to treat
as "current" and labels it **Simulation: historical replay**. Nothing in this
project claims to be live data.

## D8. The LLM chooses what to investigate; SQL computes every number
The agent may only call read-only tools that run SQL. Every number in a report
must come from a tool result, which is logged with its query. The LLM chooses
which tool to call next and writes the explanation. It never calculates numbers itself.

## D9. Free local model by default; the LLM layer can be swapped
The whole project runs at $0. The agent uses a local open-weights model served
by **Ollama** (default `qwen3:8b`, chosen because it supports tool calling and
fits on a laptop). All model calls go through one small module (`src/llm.py`)
with a single function: send messages and tool definitions, get back text or
tool calls. The backend is chosen with `LLM_PROVIDER` in `.env`; a paid API such
as Claude is an optional extra, not a requirement.
- *Why:* it costs nothing, keeps data on the machine, and anyone who clones the
  repo can run it without a paid key.
- *Trade-off:* small local models pick the wrong tool or misread a result more
  often than large hosted models, and they respond more slowly. We don't hide
  this. The safeguards in D8 (SQL computes every number; every claim must cite a
  tool result) exist partly because of it, and Stage 4 measures tool errors,
  numerical accuracy and response time for each model actually tested.
- *Portability:* the agent loop, tools and evaluation never import a provider
  SDK directly, so comparing models is a one-line config change.
- *Replay mode:* saved agent runs (tool calls, evidence, report) can be played
  back in the app with no model running at all, so the GitHub demo works for anyone.
