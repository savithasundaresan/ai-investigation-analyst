# Investigation: why did late deliveries rise in March 2018?

> **Simulation: historical replay.** Olist data; "this month" is March 2018,
> compared with February 2018. Written by hand as the reference for the agent.
> Every number is generated from `src/metrics.py` and carries an evidence ID;
> the queries are listed at the end. Regenerate with
> `uv run python -m src.investigate_feb_mar_2018`.

## Answer in brief

The late-delivery rate rose from **14.1% to 19.0%** (+4.8 pp, z = 7.6) [E1].
The rise is **not a mix effect**: under every split we tried, orders did not
shift toward slower segments (the largest mix effect is 0.4 pp, by customer state) [E3].
It was broad-based, and 69% of it lines up with **shorter
delivery promises**, not slower deliveries:

- Judged against a fixed yardstick (February's typical promise per destination
  state), deliveries got slower by only **+1.5 pp**; the remaining
  **+3.3 pp** came from estimated delivery dates moving closer [E7].
- The median promise fell from **25 to 22 days** while the median actual
  delivery time went from 14 to 13 days [E7].

**Investigate first:** what changed in how estimated delivery dates were set
from mid-February 2018, and whether the shorter promises were intended.
Second: the delivery slowdown that peaked in the week of 26 February.

## 1. Is the data trustworthy for these months?

Both months are complete under our rules [E2]: 6,555 and
7,003 delivered orders, no estimates beyond the data cutoff, and
no delivery-timestamp anomalies. One thing to keep in mind: orders that were
never delivered after their estimate passed rose from 70 to
165 [E2]. They are not in the late rate (no delivery date), so if
anything the March rate **understates** the problem.

## 2. Is the change real?

926 of 6,555 delivered orders were late in February and
1,328 of 7,003 in March [E1]. A z-score of 7.6 is far beyond
the noise threshold of 2, so this is not random month-to-month variation.

## 3. Mix or performance?

| Split by | Mix | Performance | Interaction | Evidence |
|---|---|---|---|---|
| Customer state | -0.40 pp | +5.33 pp | -0.10 pp | [E3] |
| Seller state | +0.22 pp | +4.34 pp | +0.29 pp | [E4] |
| Product category | +0.03 pp | +4.92 pp | -0.12 pp | [E6] |
| Seller | +0.06 pp | +5.02 pp | -0.24 pp | [E5] |

Under every split, almost the whole change is **performance**: segments got
worse in place, rather than volume moving toward segments that were already
slow. (At seller level, 1,192 of 1,220 sellers have fewer than 30
delivered orders in a month and are pooled into "Other", so that row says
little about individual sellers.)

The deterioration was also widespread: 6 customer states pass all three
anomaly rules [E9], and 7 of the 28 categories with enough orders
are flagged too [E10]. A problem that hits many destinations and product types
at once points to something shared (estimates, carriers), not to a few bad
sellers.

| Flagged customer state | Delivered orders (Feb → Mar) | Late rate | Change | z |
|---|---|---|---|---|
| MG | 790 → 856 | 8.4% → 20.4% | +12.1 pp | 6.9 |
| RS | 363 → 407 | 13.2% → 19.2% | +5.9 pp | 2.2 |
| BA | 208 → 235 | 19.2% → 31.5% | +12.3 pp | 2.9 |
| ES | 149 → 133 | 12.1% → 41.4% | +29.3 pp | 5.6 |
| PA | 57 → 67 | 12.3% → 50.7% | +38.5 pp | 4.5 |
| MS | 64 → 57 | 20.3% → 40.4% | +20.0 pp | 2.4 |

## 4. Slower deliveries, or shorter promises?

An order is late when actual delivery days exceed promised days, so the late
rate can rise for either reason. The two months tell different stories [E7]:

| | Feb 2018 | Mar 2018 |
|---|---|---|
| Average promised days | 25.2 | 22.7 |
| Average actual days | 16.9 | 16.2 |
| Late rate | 14.1% | 19.0% |
| Late rate against Feb's promise per state | 15.9% | 17.3% |

Average actual delivery time *fell* slightly, yet the late rate rose, because
promises shrank faster. Holding the promise fixed explains only
+1.5 pp of the +4.8 pp change.

**Clearest single example, Minas Gerais (MG):** the median promise went from
26 to 22 days, the median delivery time from 14 to 14 days, and the
late rate from 8.4% to 20.4% [E11]. Deliveries to MG didn't get slower;
the promise did get shorter.

## 5. The month boundary hides the shape of the episode

Weekly, by purchase week [E8]:

| Week of | Delivered | Late rate | Promised days | Actual days |
|---|---|---|---|---|
| 29 Jan | 1,561 | 6.5% | 27.5 | 15.6 |
| 05 Feb | 1,541 | 8.6% | 27.7 | 16.5 |
| 12 Feb | 1,595 | 12.9% | 24.0 | 16.0 |
| 19 Feb | 1,713 | 19.0% | 23.4 | 17.5 |
| 26 Feb | 1,849 | 26.3% | 22.9 | 19.0 |
| 05 Mar | 1,598 | 24.8% | 23.3 | 18.1 |
| 12 Mar | 1,541 | 21.1% | 23.1 | 16.7 |
| 19 Mar | 1,671 | 11.8% | 21.8 | 13.9 |
| 26 Mar | 1,431 | 11.1% | 22.0 | 13.8 |
| 02 Apr | 1,555 | 6.9% | 22.5 | 11.9 |

The promise dropped in mid-February and **stayed lower**. On top of that,
deliveries slowed for a few weeks: actual time peaked at 19.0 days in the week of
26 February, and the late rate peaked at 26.3% in the week of 26 February.
By April, deliveries were faster than before, so the shorter promise stopped
causing misses. In other words, the shorter promise left no buffer, and a
temporary slowdown turned into a spike in late deliveries. Comparing whole
months splits this one episode across February and March.

## 6. Did customers notice?

The low-review rate (1–2 stars) rose from 20.9% to 22.8% (+1.9 pp, z = 2.7) [E12].
That is consistent with late deliveries hurting satisfaction, but this
comparison alone doesn't prove the lateness caused it.

## Recommended next steps

1. **Review how estimated delivery dates were calculated from mid-February
   2018.** Find out whether the shorter promises were a deliberate change (for
   example, to improve conversion) and whether they were tested against actual
   delivery times.
2. **Look into the late-February delivery slowdown** with carriers, starting
   with the flagged destination states above.
3. **Monitor slack** (promised days minus actual days) next to the late rate,
   so a shrinking buffer shows up before the late rate does.

## Limitations

- **Association, not causation.** The data shows that promises shortened and
  lateness rose together. It can't tell us why the estimates changed, or
  whether something else changed at the same time.
- The "fixed yardstick" uses February's *median* promise per state; a
  different yardstick would split the change somewhat differently, though it
  would not change the conclusion that promises moved more than deliveries.
- 165 March orders were never delivered after their estimate passed, and
  they are outside the late rate.
- No revenue or savings figures are given; the data doesn't support them.

## Evidence

Each ID is a call to `src/metrics.py`; the SQL below is exactly what ran.

### E1: `compare_periods(late_rate, 2018-02, 2018-03)`

```sql
SELECT
    f.purchase_month,
    count(*)                                          AS orders,
    count(*) FILTER (WHERE f.is_delivered)            AS delivered,
    count(*) FILTER (WHERE f.is_late)                 AS late_orders,
    avg(CAST(f.is_late AS DOUBLE))                    AS late_rate,
    avg(f.days_late) FILTER (WHERE f.is_late)         AS avg_days_late,
    avg(f.delivery_days)                              AS avg_delivery_days,
    avg(f.promised_days) FILTER (WHERE f.is_delivered) AS avg_promised_days,
    count(*) FILTER (WHERE f.is_overdue_undelivered)  AS overdue_undelivered,
    avg(f.order_value)                                AS aov,
    count(*) FILTER (WHERE f.has_review)              AS reviewed,
    count(*) FILTER (WHERE f.is_low_review)           AS low_review_orders,
    avg(CAST(f.is_low_review AS DOUBLE))              AS low_review_rate,
    count(*) FILTER (WHERE f.is_canceled)             AS canceled_orders,
    avg(CAST(f.is_canceled AS DOUBLE))                AS cancellation_rate
FROM fct_orders f
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY f.purchase_month
ORDER BY f.purchase_month
```

### E2: `data_quality(2018-02, 2018-03)`

```sql
SELECT
    m.purchase_month,
    m.is_complete,
    m.n_orders,
    m.n_delivered,
    m.share_open,
    m.share_estimate_after_cutoff,
    count(*) FILTER (WHERE f.dq_delivered_without_date)   AS delivered_without_date,
    count(*) FILTER (WHERE f.dq_carrier_before_purchase)  AS carrier_before_purchase,
    count(*) FILTER (WHERE f.dq_delivered_before_carrier) AS delivered_before_carrier,
    count(*) FILTER (WHERE f.dq_no_items)                 AS no_items,
    count(*) FILTER (WHERE f.dq_payment_mismatch)         AS payment_mismatch,
    count(*) FILTER (WHERE f.is_overdue_undelivered)      AS overdue_undelivered
FROM dim_month m
JOIN fct_orders f USING (purchase_month)
WHERE m.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
ORDER BY m.purchase_month
```

### E3: `decompose_mix(late_rate, customer_state)`

```sql
WITH seg AS (
    SELECT order_id, customer_state AS segment, 1.0 AS weight FROM fct_orders
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E4: `decompose_mix(late_rate, seller_state)`

```sql
WITH seg AS (
    SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight
        FROM (SELECT DISTINCT order_id, seller_state AS segment FROM bridge_order_segment)
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E5: `decompose_mix(late_rate, seller_id)`

```sql
WITH seg AS (
    SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight
        FROM (SELECT DISTINCT order_id, seller_id AS segment FROM bridge_order_segment)
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E6: `decompose_mix(late_rate, category)`

```sql
WITH seg AS (
    SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight
        FROM (SELECT DISTINCT order_id, category AS segment FROM bridge_order_segment)
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E7: `promise_vs_speed(2018-02, 2018-03)`

```sql
WITH base_promise AS (
    -- The base month's typical promise per destination state.
    SELECT f.customer_state, median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01')
    GROUP BY 1
), overall AS (
    SELECT median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01')
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    count(*)                                                          AS delivered,
    avg(CAST(f.is_late AS DOUBLE))                                    AS late_rate,
    avg(CAST(f.delivery_days > coalesce(bp.yardstick_days, o.yardstick_days) AS DOUBLE)) AS late_rate_same_yardstick,
    avg(f.delivery_days)                                              AS avg_delivery_days,
    avg(f.promised_days)                                              AS avg_promised_days,
    median(f.delivery_days)                                           AS median_delivery_days,
    median(f.promised_days)                                           AS median_promised_days
FROM fct_orders f
CROSS JOIN overall o
LEFT JOIN base_promise bp USING (customer_state)
WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY 1
ORDER BY 1
```

### E8: `weekly(2018-01-29 to 2018-04-08)`

```sql
SELECT
    CAST(date_trunc('week', f.purchased_at) AS DATE)  AS week,
    count(*)                                          AS delivered,
    avg(CAST(f.is_late AS DOUBLE))                    AS late_rate,
    avg(f.delivery_days)                              AS avg_delivery_days,
    avg(f.promised_days)                              AS avg_promised_days
FROM fct_orders f
WHERE f.is_delivered AND f.purchased_at >= DATE '2018-01-29' AND f.purchased_at < DATE '2018-04-09' 
GROUP BY 1
ORDER BY 1
```

### E9: `break_down(late_rate, customer_state)`

```sql
WITH seg AS (
    SELECT order_id, customer_state AS segment, 1.0 AS weight FROM fct_orders
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E10: `break_down(late_rate, category)`

```sql
WITH seg AS (
    SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight
        FROM (SELECT DISTINCT order_id, category AS segment FROM bridge_order_segment)
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE f.is_delivered)                             AS n,
    count(*) FILTER (WHERE f.is_delivered AND f.is_late)                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered)         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE f.is_delivered AND f.is_late) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY ALL
```

### E11: `promise_vs_speed(2018-02, 2018-03, customer_state=MG)`

```sql
WITH base_promise AS (
    -- The base month's typical promise per destination state.
    SELECT f.customer_state, median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01') AND f.customer_state = 'MG'
    GROUP BY 1
), overall AS (
    SELECT median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01') AND f.customer_state = 'MG'
)
SELECT
    CASE WHEN f.purchase_month = DATE '2018-02-01' THEN 'base' ELSE 'current' END AS period,
    count(*)                                                          AS delivered,
    avg(CAST(f.is_late AS DOUBLE))                                    AS late_rate,
    avg(CAST(f.delivery_days > coalesce(bp.yardstick_days, o.yardstick_days) AS DOUBLE)) AS late_rate_same_yardstick,
    avg(f.delivery_days)                                              AS avg_delivery_days,
    avg(f.promised_days)                                              AS avg_promised_days,
    median(f.delivery_days)                                           AS median_delivery_days,
    median(f.promised_days)                                           AS median_promised_days
FROM fct_orders f
CROSS JOIN overall o
LEFT JOIN base_promise bp USING (customer_state)
WHERE f.is_delivered AND f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01') AND f.customer_state = 'MG'
GROUP BY 1
ORDER BY 1
```

### E12: `compare_periods(low_review_rate, 2018-02, 2018-03)`

```sql
SELECT
    f.purchase_month,
    count(*)                                          AS orders,
    count(*) FILTER (WHERE f.is_delivered)            AS delivered,
    count(*) FILTER (WHERE f.is_late)                 AS late_orders,
    avg(CAST(f.is_late AS DOUBLE))                    AS late_rate,
    avg(f.days_late) FILTER (WHERE f.is_late)         AS avg_days_late,
    avg(f.delivery_days)                              AS avg_delivery_days,
    avg(f.promised_days) FILTER (WHERE f.is_delivered) AS avg_promised_days,
    count(*) FILTER (WHERE f.is_overdue_undelivered)  AS overdue_undelivered,
    avg(f.order_value)                                AS aov,
    count(*) FILTER (WHERE f.has_review)              AS reviewed,
    count(*) FILTER (WHERE f.is_low_review)           AS low_review_orders,
    avg(CAST(f.is_low_review AS DOUBLE))              AS low_review_rate,
    count(*) FILTER (WHERE f.is_canceled)             AS canceled_orders,
    avg(CAST(f.is_canceled AS DOUBLE))                AS cancellation_rate
FROM fct_orders f
WHERE f.purchase_month IN (DATE '2018-02-01', DATE '2018-03-01')
GROUP BY f.purchase_month
ORDER BY f.purchase_month
```
