-- STAGING: one stg_ table per raw_ table, same grain.
-- Only types, names and trivial cleaning here; no business logic, no joins
-- except the category translation lookup.

CREATE OR REPLACE TABLE stg_orders AS
SELECT
    order_id,
    customer_id,
    order_status,
    CAST(order_purchase_timestamp      AS TIMESTAMP) AS purchased_at,
    CAST(order_approved_at             AS TIMESTAMP) AS approved_at,
    CAST(order_delivered_carrier_date  AS TIMESTAMP) AS delivered_carrier_at,
    CAST(order_delivered_customer_date AS TIMESTAMP) AS delivered_customer_at,
    -- Stored as midnight timestamps in the CSV; it is really a date.
    CAST(CAST(order_estimated_delivery_date AS TIMESTAMP) AS DATE) AS estimated_delivery_date
FROM raw_orders;

-- customer_id is per order (one per order); customer_unique_id is the person.
CREATE OR REPLACE TABLE stg_customers AS
SELECT
    customer_id,
    customer_unique_id,
    customer_zip_code_prefix AS customer_zip_prefix,
    customer_city,
    upper(trim(customer_state)) AS customer_state
FROM raw_customers;

CREATE OR REPLACE TABLE stg_order_items AS
SELECT
    order_id,
    CAST(order_item_id AS INTEGER)        AS order_item_id,
    product_id,
    seller_id,
    CAST(shipping_limit_date AS TIMESTAMP) AS shipping_limit_at,
    CAST(price AS DECIMAL(12, 2))          AS price,
    CAST(freight_value AS DECIMAL(12, 2))  AS freight_value
FROM raw_order_items;

CREATE OR REPLACE TABLE stg_order_payments AS
SELECT
    order_id,
    CAST(payment_sequential AS INTEGER)    AS payment_sequential,
    payment_type,
    CAST(payment_installments AS INTEGER)  AS payment_installments,
    CAST(payment_value AS DECIMAL(12, 2))  AS payment_value
FROM raw_order_payments;

CREATE OR REPLACE TABLE stg_order_reviews AS
SELECT
    review_id,
    order_id,
    CAST(review_score AS INTEGER)              AS review_score,
    CAST(review_creation_date AS TIMESTAMP)    AS review_created_at,
    CAST(review_answer_timestamp AS TIMESTAMP) AS review_answered_at,
    review_comment_message IS NOT NULL         AS has_comment
FROM raw_order_reviews;

-- 610 products have no category -> 'unknown'. Two Portuguese categories are
-- missing from the translation file; they are translated here by hand.
CREATE OR REPLACE TABLE stg_products AS
SELECT
    p.product_id,
    CASE
        WHEN p.product_category_name IS NULL THEN 'unknown'
        WHEN t.product_category_name_english IS NOT NULL THEN t.product_category_name_english
        WHEN p.product_category_name = 'pc_gamer' THEN 'pc_gamer'
        WHEN p.product_category_name = 'portateis_cozinha_e_preparadores_de_alimentos'
            THEN 'portable_kitchen_and_food_preparers'
        ELSE p.product_category_name
    END AS category,
    CAST(p.product_weight_g AS INTEGER) AS weight_g
FROM raw_products p
LEFT JOIN raw_category_translation t USING (product_category_name);

CREATE OR REPLACE TABLE stg_sellers AS
SELECT
    seller_id,
    seller_zip_code_prefix AS seller_zip_prefix,
    seller_city,
    upper(trim(seller_state)) AS seller_state
FROM raw_sellers;
