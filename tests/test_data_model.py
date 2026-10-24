"""Integrity tests for the data model. Run after `uv run python -m src.load`.

The point of these tests is that every total in the marts can be reconciled
with the source, so no number on the dashboard or in an agent report is
inflated by a join.
"""

import duckdb
import pytest

from src.config import DB_PATH


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("database not built; run `uv run python -m src.load`")
    with duckdb.connect(str(DB_PATH), read_only=True) as c:
        yield c


def one(con, sql):
    return con.execute(sql).fetchone()


# --- Source -----------------------------------------------------------------

def test_raw_row_counts_match_published_olist_dataset(con):
    expected = {
        "raw_orders": 99_441,
        "raw_customers": 99_441,
        "raw_order_items": 112_650,
        "raw_order_payments": 103_886,
        "raw_order_reviews": 99_224,
        "raw_products": 32_951,
        "raw_sellers": 3_095,
        "raw_category_translation": 71,
    }
    for table, n in expected.items():
        assert one(con, f"SELECT count(*) FROM {table}")[0] == n, table


# --- Grain ------------------------------------------------------------------

@pytest.mark.parametrize("table", ["fct_orders", "int_order_items", "int_order_payments", "int_order_reviews"])
def test_one_row_per_order(con, table):
    n, n_distinct = one(con, f"SELECT count(*), count(DISTINCT order_id) FROM {table}")
    assert n == n_distinct


def test_fct_orders_keeps_every_order(con):
    assert one(con, "SELECT count(*) FROM fct_orders")[0] == one(con, "SELECT count(*) FROM raw_orders")[0]


def test_bridge_grain_is_order_seller_category(con):
    n, n_distinct = one(con, """
        SELECT count(*), count(DISTINCT (order_id, seller_id, category)) FROM bridge_order_segment""")
    assert n == n_distinct


# --- Totals reconcile with the source (no fan-out) ---------------------------

def test_order_value_total_matches_items(con):
    mart = one(con, "SELECT sum(order_value) FROM fct_orders")[0]
    src = one(con, "SELECT sum(price + freight_value) FROM stg_order_items")[0]
    assert mart == src


def test_payment_total_matches_payments(con):
    mart = one(con, "SELECT sum(payment_total) FROM fct_orders")[0]
    src = one(con, "SELECT sum(payment_value) FROM stg_order_payments")[0]
    assert mart == src


def test_bridge_value_and_orders_match_items(con):
    value, orders = one(con, "SELECT sum(segment_value), count(DISTINCT order_id) FROM bridge_order_segment")
    src_value, src_orders = one(con, "SELECT sum(price + freight_value), count(DISTINCT order_id) FROM stg_order_items")
    assert value == src_value
    assert orders == src_orders


def test_naive_join_would_inflate_payments(con):
    """Documents the trap that the int_ layer avoids: joining items and
    payments at their native grain counts multi-item orders' payments repeatedly."""
    naive = one(con, """
        SELECT sum(p.payment_value)
        FROM stg_order_payments p JOIN stg_order_items i USING (order_id)""")[0]
    correct = one(con, "SELECT sum(payment_value) FROM stg_order_payments WHERE order_id IN (SELECT order_id FROM stg_order_items)")[0]
    assert float(naive) > float(correct) * 1.1


# --- Metric logic -----------------------------------------------------------

def test_is_late_defined_only_for_delivered_orders(con):
    bad = one(con, "SELECT count(*) FROM fct_orders WHERE (is_late IS NULL) = is_delivered")[0]
    assert bad == 0


def test_late_rate_matches_independent_calculation_from_raw(con):
    """Recompute March 2018 from the raw text columns, on a different code path."""
    independent = one(con, """
        SELECT avg(CASE WHEN CAST(LEFT(order_delivered_customer_date, 10) AS DATE)
                           > CAST(LEFT(order_estimated_delivery_date, 10) AS DATE) THEN 1.0 ELSE 0.0 END)
        FROM raw_orders
        WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
          AND order_purchase_timestamp >= '2018-03-01' AND order_purchase_timestamp < '2018-04-01'""")[0]
    mart = one(con, "SELECT avg(CAST(is_late AS DOUBLE)) FROM fct_orders WHERE purchase_month = DATE '2018-03-01'")[0]
    assert mart == pytest.approx(independent, abs=1e-12)


def test_days_late_positive_iff_late(con):
    bad = one(con, "SELECT count(*) FROM fct_orders WHERE is_delivered AND (days_late > 0) <> is_late")[0]
    assert bad == 0


def test_low_review_is_score_one_or_two(con):
    bad = one(con, "SELECT count(*) FROM fct_orders WHERE has_review AND is_low_review <> (review_score <= 2)")[0]
    assert bad == 0
    assert one(con, "SELECT min(review_score), max(review_score) FROM fct_orders")[:2] == (1, 5)


def test_every_category_is_named(con):
    assert one(con, "SELECT count(*) FROM stg_products WHERE category IS NULL")[0] == 0


# --- Analysis window ---------------------------------------------------------

def test_complete_months_are_jan_2017_to_aug_2018(con):
    months = [r[0].isoformat() for r in con.execute(
        "SELECT purchase_month FROM dim_month WHERE is_complete ORDER BY 1").fetchall()]
    assert months[0] == "2017-01-01"
    assert months[-1] == "2018-08-01"
    assert len(months) == 20  # contiguous
