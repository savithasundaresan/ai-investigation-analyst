-- INTERMEDIATE: collapse each one-to-many child table to exactly one row per
-- order BEFORE anything is joined (decision D1). Joining items, payments and
-- reviews at their native grain multiplies rows and inflates totals.

-- Items: 9,803 orders have more than one item; 1,278 have more than one seller.
CREATE OR REPLACE TABLE int_order_items AS
SELECT
    order_id,
    count(*)                    AS n_items,
    count(DISTINCT seller_id)   AS n_sellers,
    count(DISTINCT product_id)  AS n_products,
    sum(price)                  AS items_price,
    sum(freight_value)          AS items_freight,
    sum(price + freight_value)  AS items_value
FROM stg_order_items
GROUP BY order_id;

-- Payments: 2,961 orders were paid in more than one payment row
-- (split cards, vouchers).
CREATE OR REPLACE TABLE int_order_payments AS
SELECT
    order_id,
    count(*)                                    AS n_payments,
    sum(payment_value)                          AS payment_total,
    arg_max(payment_type, payment_value)        AS main_payment_type,
    max(payment_installments)                   AS max_installments
FROM stg_order_payments
GROUP BY order_id;

-- Reviews: 547 orders have more than one review. Keep the most recent one,
-- because it reflects the customer's final opinion. Ties are broken by
-- review_id so the result is deterministic.
CREATE OR REPLACE TABLE int_order_reviews AS
SELECT
    order_id,
    count(*) AS n_reviews,
    arg_max(review_score, (review_created_at, review_answered_at, review_id)) AS review_score,
    arg_max(has_comment,  (review_created_at, review_answered_at, review_id)) AS has_comment
FROM stg_order_reviews
GROUP BY order_id;
