"""Independent answer key for the evaluation.

Written from scratch against the STAGING tables (stg_orders, stg_customers,
stg_order_items, stg_sellers). It does not use fct_orders, dim_month,
bridge_order_segment or src/metrics.py, so it is a second implementation of
every definition the agent's tools rely on. tests/test_evaluation.py checks
that the two implementations agree.

Each query can be pasted into the DuckDB shell to verify an answer by hand:

    duckdb -readonly data/processed/olist.duckdb
    -- then replace each ? with a month, e.g. DATE '2018-02-01'

    uv run python -m src.answer_key 2018-02 2018-03   # prints one answer
"""

import datetime as dt
import math
import sys

import pandas as pd

from src.config import MIN_ABS_Z, MIN_N
from src.db import connect

# One row per order, rebuilt from staging. Late = delivered on a later calendar
# day than the estimate, counted only for orders that were actually delivered.
ORDERS = """
WITH o AS (
    SELECT
        o.order_id,
        c.customer_state,
        CAST(date_trunc('month', o.purchased_at) AS DATE)                  AS month,
        o.order_status = 'delivered' AND o.delivered_customer_at IS NOT NULL AS delivered,
        o.order_status IN ('canceled', 'unavailable')                       AS canceled,
        CAST(o.delivered_customer_at AS DATE) > o.estimated_delivery_date   AS late,
        date_diff('day', CAST(o.purchased_at AS DATE), CAST(o.delivered_customer_at AS DATE)) AS took_days,
        date_diff('day', CAST(o.purchased_at AS DATE), o.estimated_delivery_date)             AS promised_days,
        o.estimated_delivery_date > (SELECT CAST(max(delivered_customer_at) AS DATE) FROM stg_orders) AS estimate_after_cutoff
    FROM stg_orders o
    JOIN stg_customers c USING (customer_id)
)"""

# AK1. Is each month complete? (>= 500 orders, <= 5% still open, <= 1% estimates after the data cutoff)
MONTH_STATUS_SQL = ORDERS + """
SELECT
    month,
    count(*)                                                       AS orders,
    avg(CASE WHEN NOT delivered AND NOT canceled THEN 1 ELSE 0 END) AS open_share,
    avg(CASE WHEN estimate_after_cutoff THEN 1 ELSE 0 END)          AS estimate_after_cutoff_share
FROM o
WHERE month IN (?, ?)
GROUP BY month"""

# AK2. Late rate per month: late delivered orders / delivered orders.
LATE_RATE_SQL = ORDERS + """
SELECT month, count(*) AS delivered, count(*) FILTER (WHERE late) AS late
FROM o
WHERE delivered AND month IN (?, ?)
GROUP BY month"""

# AK3. Slower deliveries or shorter promises? Re-score both months against the
# base month's median promise per customer state (overall median as fallback).
PROMISE_SQL = ORDERS + """,
yardstick AS (
    SELECT customer_state, median(promised_days) AS days FROM o WHERE delivered AND month = ? GROUP BY 1
),
fallback AS (
    SELECT median(promised_days) AS days FROM o WHERE delivered AND month = ?
)
SELECT
    o.month,
    avg(CASE WHEN o.late THEN 1 ELSE 0 END)                                        AS late_rate,
    avg(CASE WHEN o.took_days > coalesce(y.days, f.days) THEN 1 ELSE 0 END)        AS late_rate_fixed_promise
FROM o
LEFT JOIN yardstick y USING (customer_state)
CROSS JOIN fallback f
WHERE o.delivered AND o.month IN (?, ?)
GROUP BY o.month"""

# AK4. Late counts per customer state (each order counts once).
CUSTOMER_STATE_SQL = ORDERS + """
SELECT month, customer_state AS segment,
       count(*) AS n, count(*) AS w_n, count(*) FILTER (WHERE late) AS w_x
FROM o
WHERE delivered AND month IN (?, ?)
GROUP BY ALL"""

# AK5. Late counts per seller state. An order shipped from k seller states counts
# 1/k towards each, so every order adds up to exactly one.
SELLER_STATE_SQL = ORDERS + """,
pairs AS (
    SELECT DISTINCT i.order_id, s.seller_state AS segment
    FROM stg_order_items i JOIN stg_sellers s USING (seller_id)
),
weighted AS (
    SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight FROM pairs
)
SELECT o.month, coalesce(w.segment, '(no items)') AS segment,
       count(*) AS n,
       sum(coalesce(w.weight, 1.0)) AS w_n,
       sum(CASE WHEN o.late THEN coalesce(w.weight, 1.0) ELSE 0 END) AS w_x
FROM o
LEFT JOIN weighted w USING (order_id)
WHERE o.delivered AND o.month IN (?, ?)
GROUP BY ALL"""


def _month(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[5:7]), 1)


def _z(x0, n0, x1, n1) -> float:
    p = (x0 + x1) / (n0 + n1)
    se = math.sqrt(p * (1 - p) * (1 / n0 + 1 / n1))
    return 0.0 if se == 0 else (x1 / n1 - x0 / n0) / se


def top_contributor(df: pd.DataFrame, b: dt.date, c: dt.date) -> dict:
    """The segment that moved the overall rate most: share_current * rate_current
    - share_base * rate_base (which equals its mix + performance + interaction).
    Segments under MIN_N orders in either month are pooled into 'Other' first,
    and 'Other' itself is never the answer."""
    df = df.assign(period=["base" if pd.Timestamp(m).date() == b else "current" for m in df["month"]])
    wide = df.pivot_table(index="segment", columns="period", values=["n", "w_n", "w_x"], aggfunc="sum", fill_value=0)
    big = (wide[("n", "base")] >= MIN_N) & (wide[("n", "current")] >= MIN_N)
    g = wide.groupby(wide.index.where(big, "Other")).sum()
    contrib = 100 * (g[("w_x", "current")] / g[("w_n", "current")].sum()   # share * rate = w_x / total w_n
                     - g[("w_x", "base")] / g[("w_n", "base")].sum())
    named = contrib.drop("Other", errors="ignore")
    seg = named.abs().idxmax()
    return {"segment": seg, "contribution_pp": round(float(named[seg]), 2)}


def answer(con, base: str, current: str) -> dict:
    """The expected answer for one question."""
    b, c = _month(base), _month(current)
    status = {r[0]: r for r in con.execute(MONTH_STATUS_SQL, [b, c]).fetchall()}
    complete = {}
    for label, m in (("base", b), ("current", c)):
        r = status.get(m)
        complete[label] = bool(r and r[1] >= 500 and r[2] <= 0.05 and r[3] <= 0.01)
    out = {"base": base, "current": current, "base_complete": complete["base"],
           "current_complete": complete["current"],
           "base_orders": int(status[b][1]) if b in status else 0,
           "current_orders": int(status[c][1]) if c in status else 0}
    out["expect_insufficient"] = not (complete["base"] and complete["current"])
    if out["expect_insufficient"]:
        return out

    rates = {r[0]: (r[1], r[2]) for r in con.execute(LATE_RATE_SQL, [b, c]).fetchall()}
    (n0, x0), (n1, x1) = rates[b], rates[c]
    z = _z(x0, n0, x1, n1)
    out |= {"base_rate_pct": 100 * x0 / n0, "current_rate_pct": 100 * x1 / n1,
            "change_pp": 100 * (x1 / n1 - x0 / n0), "z": z,
            "direction": "up" if x1 / n1 > x0 / n0 else "down",
            "beyond_noise": abs(z) >= MIN_ABS_Z and min(n0, n1) >= MIN_N}

    p = {r[0]: r for r in con.execute(PROMISE_SQL, [b, b, b, c]).fetchall()}
    delivery = 100 * (p[c][2] - p[b][2])
    out |= {"delivery_effect_pp": delivery, "promise_effect_pp": out["change_pp"] - delivery}
    out["dominant_effect"] = "delivery" if abs(delivery) >= abs(out["promise_effect_pp"]) else "promise"

    for dim, sql in (("customer_state", CUSTOMER_STATE_SQL), ("seller_state", SELLER_STATE_SQL)):
        df = con.execute(sql, [b, c]).df()
        out[f"top_{dim}"] = top_contributor(df, b, c)
    return out


if __name__ == "__main__":
    base, current = sys.argv[1], sys.argv[2]
    for k, v in answer(connect(), base, current).items():
        print(f"{k:22} {v}")
