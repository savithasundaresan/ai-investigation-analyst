-- MARTS: the only tables the dashboard, agent and evaluation read.

-- Last moment anything happened in the data. Used to decide which months are
-- complete and which open orders are overdue. (2018-10-17)
CREATE OR REPLACE TABLE meta_data_cutoff AS
SELECT max(delivered_customer_at) AS data_cutoff_at FROM stg_orders;

-- fct_orders: exactly one row per order (99,441).
CREATE OR REPLACE TABLE fct_orders AS
WITH base AS (
    SELECT
        o.*,
        c.customer_unique_id,
        c.customer_state,
        CAST(date_trunc('month', o.purchased_at) AS DATE) AS purchase_month,
        o.order_status IN ('canceled', 'unavailable') AS is_canceled,
        -- Delivered = final status AND a delivery date. 8 'delivered' orders
        -- have no delivery date; 6 'canceled' orders have one. Both excluded.
        (o.order_status = 'delivered' AND o.delivered_customer_at IS NOT NULL) AS is_delivered
    FROM stg_orders o
    JOIN stg_customers c USING (customer_id)
)
SELECT
    b.order_id,
    b.customer_id,
    b.customer_unique_id,
    b.customer_state,
    b.order_status,
    b.purchased_at,
    b.purchase_month,
    b.estimated_delivery_date,
    b.delivered_carrier_at,
    b.delivered_customer_at,

    -- Outcomes ----------------------------------------------------------
    b.is_canceled,
    b.is_delivered,
    -- Late = delivered on a later calendar day than the estimate (D2).
    -- NULL (not FALSE) for undelivered orders, so AVG(is_late) only counts
    -- delivered orders.
    CASE WHEN b.is_delivered
         THEN CAST(b.delivered_customer_at AS DATE) > b.estimated_delivery_date END AS is_late,
    CASE WHEN b.is_delivered
         THEN greatest(date_diff('day', b.estimated_delivery_date, CAST(b.delivered_customer_at AS DATE)), 0) END AS days_late,
    CASE WHEN b.is_delivered
         THEN date_diff('day', CAST(b.purchased_at AS DATE), CAST(b.delivered_customer_at AS DATE)) END AS delivery_days,
    -- The promise the late flag is measured against. A late rate can rise
    -- because deliveries got slower OR because promises got shorter.
    date_diff('day', CAST(b.purchased_at AS DATE), b.estimated_delivery_date) AS promised_days,
    -- Never delivered, not canceled, and the estimate passed before the data
    -- cutoff: effectively lost or stuck. Not in the late rate (no delivery
    -- date) but reported alongside it so it isn't silently ignored.
    (NOT b.is_delivered AND NOT b.is_canceled AND b.order_status <> 'delivered'
        AND b.estimated_delivery_date < CAST(m.data_cutoff_at AS DATE)) AS is_overdue_undelivered,

    -- Value -------------------------------------------------------------
    coalesce(i.n_items, 0)   AS n_items,
    coalesce(i.n_sellers, 0) AS n_sellers,
    -- Order value = items price + freight. Chosen over payments because it
    -- can be split per seller and per category; payments cannot.
    i.items_value            AS order_value,
    p.payment_total,
    p.main_payment_type,

    -- Reviews -----------------------------------------------------------
    r.review_score IS NOT NULL AS has_review,
    r.review_score,
    CASE WHEN r.review_score IS NOT NULL THEN r.review_score <= 2 END AS is_low_review,

    -- Data-quality flags (kept, not dropped; see check_data_quality) -----
    (b.order_status = 'delivered' AND b.delivered_customer_at IS NULL) AS dq_delivered_without_date,
    (b.delivered_carrier_at < b.purchased_at)                         AS dq_carrier_before_purchase,
    (b.delivered_customer_at < b.delivered_carrier_at)                AS dq_delivered_before_carrier,
    (i.order_id IS NULL)                                              AS dq_no_items,
    (p.order_id IS NULL)                                              AS dq_no_payment,
    (abs(p.payment_total - i.items_value) > 1)                        AS dq_payment_mismatch
FROM base b
CROSS JOIN meta_data_cutoff m
LEFT JOIN int_order_items    i USING (order_id)
LEFT JOIN int_order_payments p USING (order_id)
LEFT JOIN int_order_reviews  r USING (order_id);

-- bridge_order_segment: one row per order x seller x category (D4).
-- Used for seller / seller-state / category breakdowns. Join it to
-- fct_orders for outcomes and count DISTINCT order_id per segment.
CREATE OR REPLACE TABLE bridge_order_segment AS
SELECT
    it.order_id,
    it.seller_id,
    s.seller_state,
    p.category,
    count(*)                         AS n_items,
    sum(it.price + it.freight_value) AS segment_value
FROM stg_order_items it
JOIN stg_sellers  s USING (seller_id)
JOIN stg_products p USING (product_id)
GROUP BY ALL;

-- dim_month: which purchase months are complete enough to analyse (D3).
-- A month is complete when it has enough orders, few orders still in an
-- open status, and almost no estimates falling after the data cutoff
-- (otherwise its late orders may simply not have arrived yet).
CREATE OR REPLACE TABLE dim_month AS
WITH m AS (
    SELECT
        purchase_month,
        count(*)                                                    AS n_orders,
        count(*) FILTER (WHERE is_delivered)                        AS n_delivered,
        avg(CASE WHEN NOT is_delivered AND NOT is_canceled THEN 1 ELSE 0 END) AS share_open,
        avg(CASE WHEN estimated_delivery_date > CAST(c.data_cutoff_at AS DATE) THEN 1 ELSE 0 END) AS share_estimate_after_cutoff
    FROM fct_orders
    CROSS JOIN meta_data_cutoff c
    GROUP BY purchase_month
)
SELECT
    *,
    (n_orders >= 500 AND share_open <= 0.05 AND share_estimate_after_cutoff <= 0.01) AS is_complete
FROM m
ORDER BY purchase_month;
