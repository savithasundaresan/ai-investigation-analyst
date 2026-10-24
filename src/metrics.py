"""Metric functions shared by the dashboard, the agent's tools and the evaluation.

Every function returns a Result: the numbers plus the exact SQL and parameters
that produced them, so any number shown anywhere can be traced to its query.
SQL does the counting; Python only does the arithmetic on those counts
(shares, deltas, z-scores, the mix decomposition).
"""

import datetime as dt
import math
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from src.config import MIN_ABS_Z, MIN_DELTA_PP, MIN_N

# Rate metrics: numerator flag and denominator filter on fct_orders.
RATE_METRICS = {
    "late_rate": {"label": "Late-delivery rate", "num": "is_late", "den": "is_delivered", "x": "late_orders", "n": "delivered"},
    "low_review_rate": {"label": "Low-review rate", "num": "is_low_review", "den": "has_review", "x": "low_review_orders", "n": "reviewed"},
    "cancellation_rate": {"label": "Cancellation rate", "num": "is_canceled", "den": "TRUE", "x": "canceled_orders", "n": "orders"},
}
# Other KPIs that can be compared between periods (no z-test).
VALUE_METRICS = {
    "orders": "Orders",
    "aov": "Average order value (R$)",
    "avg_days_late": "Average days late (late orders)",
    "avg_delivery_days": "Average delivery time (days)",
    "avg_promised_days": "Average promised delivery window (days)",
    "overdue_undelivered": "Overdue and undelivered orders",
}
DIMENSIONS = {
    "customer_state": "Customer state",
    "seller_state": "Seller state",
    "seller_id": "Seller",
    "category": "Product category",
}
FILTER_KEYS = tuple(DIMENSIONS)


@dataclass
class Result:
    data: pd.DataFrame
    sql: str
    params: list
    notes: dict = field(default_factory=dict)

    def sql_with_params(self) -> str:
        """The query with its parameters written in, for display as evidence."""
        out = self.sql
        for p in self.params:
            lit = f"DATE '{p.isoformat()}'" if isinstance(p, dt.date) else f"'{p}'" if isinstance(p, str) else str(p)
            out = out.replace("?", lit, 1)
        return out


# --- Helpers -----------------------------------------------------------------

def _where(filters: dict | None, months: list[dt.date] | None) -> tuple[str, list]:
    clauses, params = [], []
    if months is None:
        clauses.append("f.purchase_month IN (SELECT purchase_month FROM dim_month WHERE is_complete)")
    else:
        clauses.append(f"f.purchase_month IN ({', '.join('?' for _ in months)})")
        params += list(months)
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if key not in FILTER_KEYS:
            raise ValueError(f"unknown filter {key!r}; expected one of {FILTER_KEYS}")
        if key == "customer_state":
            clauses.append("f.customer_state = ?")
        else:
            clauses.append(f"f.order_id IN (SELECT order_id FROM bridge_order_segment WHERE {key} = ?)")
        params.append(value)
    return " AND ".join(clauses), params


def _metric(metric: str) -> dict:
    if metric not in RATE_METRICS:
        raise ValueError(f"unknown rate metric {metric!r}; expected one of {tuple(RATE_METRICS)}")
    return RATE_METRICS[metric]


def two_prop_z(x0: float, n0: float, x1: float, n1: float) -> float:
    """z-score for the change from x0/n0 to x1/n1 (pooled two-proportion test)."""
    if n0 == 0 or n1 == 0:
        return float("nan")
    p = (x0 + x1) / (n0 + n1)
    se = math.sqrt(p * (1 - p) * (1 / n0 + 1 / n1))
    return 0.0 if se == 0 else (x1 / n1 - x0 / n0) / se


def complete_months(con: duckdb.DuckDBPyConnection) -> list[dt.date]:
    return [r[0] for r in con.execute("SELECT purchase_month FROM dim_month WHERE is_complete ORDER BY 1").fetchall()]


def previous_month(month: dt.date) -> dt.date:
    return (month.replace(day=1) - dt.timedelta(days=1)).replace(day=1)


# --- KPIs ----------------------------------------------------------------------

KPI_SQL = """
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
WHERE {where}
GROUP BY f.purchase_month
ORDER BY f.purchase_month"""


def kpis(con, months: list[dt.date] | None = None, filters: dict | None = None) -> Result:
    """All KPIs per purchase month. months=None means every complete month (the trend)."""
    where, params = _where(filters, months)
    sql = KPI_SQL.format(where=where)
    df = con.execute(sql, params).df()
    df["aov"] = df["aov"].astype(float)
    return Result(df, sql, params)


def compare_periods(con, metric: str, base: dt.date, current: dt.date, filters: dict | None = None) -> Result:
    """One metric in two months: values, change, and for rates a z-score."""
    if metric not in RATE_METRICS and metric not in VALUE_METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    k = kpis(con, [base, current], filters)
    by_month = {r.purchase_month.date() if hasattr(r.purchase_month, "date") else r.purchase_month: r
                for r in k.data.itertuples(index=False)}
    b, c = by_month.get(base), by_month.get(current)
    row = {"metric": metric, "base_month": base, "current_month": current,
           "base": getattr(b, metric, None) if b else None,
           "current": getattr(c, metric, None) if c else None}
    if metric in RATE_METRICS and b and c:
        m = RATE_METRICS[metric]
        row |= {"base_n": getattr(b, m["n"]), "current_n": getattr(c, m["n"]),
                "base_x": getattr(b, m["x"]), "current_x": getattr(c, m["x"])}
        row["delta_pp"] = 100 * (row["current"] - row["base"])
        row["z"] = two_prop_z(row["base_x"], row["base_n"], row["current_x"], row["current_n"])
    elif b and c and row["base"] is not None and row["current"] is not None:
        row["delta"] = row["current"] - row["base"]
    return Result(pd.DataFrame([row]), k.sql, k.params)


# --- Segment breakdown and mix decomposition ----------------------------------

def _segment_sql(dimension: str) -> str:
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; expected one of {tuple(DIMENSIONS)}")
    if dimension == "customer_state":
        return "SELECT order_id, customer_state AS segment, 1.0 AS weight FROM fct_orders"
    # An order split across k sellers (or categories) gives each a weight of 1/k,
    # so weights add up to exactly one per order (decision D4).
    return f"""SELECT order_id, segment, 1.0 / count(*) OVER (PARTITION BY order_id) AS weight
        FROM (SELECT DISTINCT order_id, {dimension} AS segment FROM bridge_order_segment)"""


SEGMENT_SQL = """
WITH seg AS (
    {seg}
)
SELECT
    CASE WHEN f.purchase_month = ? THEN 'base' ELSE 'current' END AS period,
    coalesce(s.segment, '(no items)')                         AS segment,
    count(*) FILTER (WHERE {den})                             AS n,
    count(*) FILTER (WHERE {den} AND {num})                   AS x,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE {den})         AS w_n,
    sum(coalesce(s.weight, 1.0)) FILTER (WHERE {den} AND {num}) AS w_x
FROM fct_orders f
LEFT JOIN seg s USING (order_id)
WHERE {where}
GROUP BY ALL"""


def _segment_counts(con, metric, dimension, base, current, filters) -> tuple[pd.DataFrame, str, list]:
    m = _metric(metric)
    where, wparams = _where(filters, [base, current])
    sql = SEGMENT_SQL.format(seg=_segment_sql(dimension), den=f"f.{m['den']}" if m["den"] != "TRUE" else "TRUE",
                             num=f"f.{m['num']}", where=where)
    params = [base] + wparams
    raw = con.execute(sql, params).df()
    wide = raw.pivot_table(index="segment", columns="period", values=["n", "x", "w_n", "w_x"], aggfunc="sum", fill_value=0)
    wide.columns = [f"{v}_{p}" for v, p in wide.columns]
    for col in ("n", "x", "w_n", "w_x"):
        for p in ("base", "current"):
            if f"{col}_{p}" not in wide:
                wide[f"{col}_{p}"] = 0
    return wide.reset_index(), sql, params


def break_down(con, metric: str, dimension: str, base: dt.date, current: dt.date,
               filters: dict | None = None) -> Result:
    """Per-segment rates in both months, with sample-size checks and anomaly flags.

    n / rate use distinct orders (what a person would count). share uses the
    fractional weights, so shares add up to 100%."""
    w, sql, params = _segment_counts(con, metric, dimension, base, current, filters)
    df = pd.DataFrame({"segment": w["segment"], "n_base": w["n_base"].astype(int), "n_current": w["n_current"].astype(int)})
    df["rate_base"] = w["x_base"] / w["n_base"].where(w["n_base"] > 0)
    df["rate_current"] = w["x_current"] / w["n_current"].where(w["n_current"] > 0)
    df["delta_pp"] = 100 * (df["rate_current"] - df["rate_base"])
    df["z"] = [two_prop_z(a, b, c, d) for a, b, c, d in zip(w["x_base"], w["n_base"], w["x_current"], w["n_current"])]
    df["share_base"] = w["w_n_base"] / w["w_n_base"].sum()
    df["share_current"] = w["w_n_current"] / w["w_n_current"].sum()
    df["meets_min_n"] = (df["n_base"] >= MIN_N) & (df["n_current"] >= MIN_N)
    df["flagged"] = df["meets_min_n"] & (df["z"].abs() >= MIN_ABS_Z) & (df["delta_pp"].abs() >= MIN_DELTA_PP)
    df = df.sort_values("n_current", ascending=False, ignore_index=True)
    notes = {"metric": metric, "dimension": dimension, "base": base, "current": current,
             "rules": {"min_n": MIN_N, "min_abs_z": MIN_ABS_Z, "min_delta_pp": MIN_DELTA_PP},
             "n_flagged": int(df["flagged"].sum())}
    return Result(df, sql, params, notes)


def decompose_mix(con, metric: str, dimension: str, base: dt.date, current: dt.date,
                  filters: dict | None = None, min_n: int = MIN_N) -> Result:
    """Split the change in a rate into mix, performance and interaction (decision D5).

        Δ overall = Σ Δshare·base_rate + Σ base_share·Δrate + Σ Δshare·Δrate

    Segments below min_n orders in either month are pooled into "Other" so
    their noisy rates don't dominate. The three parts always add up exactly
    to the change in the overall rate."""
    w, sql, params = _segment_counts(con, metric, dimension, base, current, filters)
    small = (w["n_base"] < min_n) | (w["n_current"] < min_n)
    w["group"] = w["segment"].where(~small, "Other")
    g = w.groupby("group", as_index=False)[["n_base", "n_current", "w_n_base", "w_n_current", "w_x_base", "w_x_current"]].sum()
    g["share_base"] = g["w_n_base"] / g["w_n_base"].sum()
    g["share_current"] = g["w_n_current"] / g["w_n_current"].sum()
    rb = g["w_x_base"] / g["w_n_base"].where(g["w_n_base"] > 0)
    rc = g["w_x_current"] / g["w_n_current"].where(g["w_n_current"] > 0)
    # A group missing in one month has no rate there; borrow the other month's
    # so its whole effect counts as mix. The identity holds for any choice.
    g["rate_base"], g["rate_current"] = rb.fillna(rc), rc.fillna(rb)
    d_share = g["share_current"] - g["share_base"]
    d_rate = g["rate_current"] - g["rate_base"]
    g["mix_pp"] = 100 * d_share * g["rate_base"]
    g["performance_pp"] = 100 * g["share_base"] * d_rate
    g["interaction_pp"] = 100 * d_share * d_rate
    g["total_pp"] = g["mix_pp"] + g["performance_pp"] + g["interaction_pp"]
    g = g.rename(columns={"group": "segment"})
    g = g[["segment", "n_base", "n_current", "share_base", "share_current", "rate_base", "rate_current",
           "mix_pp", "performance_pp", "interaction_pp", "total_pp"]]
    g = g.reindex(g["total_pp"].abs().sort_values(ascending=False).index).reset_index(drop=True)

    base_rate = float(w["w_x_base"].sum() / w["w_n_base"].sum())
    current_rate = float(w["w_x_current"].sum() / w["w_n_current"].sum())
    notes = {
        "metric": metric, "dimension": dimension, "base": base, "current": current, "min_n": min_n,
        "base_rate": base_rate, "current_rate": current_rate,
        "delta_pp": 100 * (current_rate - base_rate),
        "mix_pp": float(g["mix_pp"].sum()),
        "performance_pp": float(g["performance_pp"].sum()),
        "interaction_pp": float(g["interaction_pp"].sum()),
        "n_segments": int(len(w)), "n_pooled_into_other": int(small.sum()),
    }
    return Result(g, sql, params, notes)


WEEKLY_SQL = """
SELECT
    CAST(date_trunc('week', f.purchased_at) AS DATE)  AS week,
    count(*)                                          AS delivered,
    avg(CAST(f.is_late AS DOUBLE))                    AS late_rate,
    avg(f.delivery_days)                              AS avg_delivery_days,
    avg(f.promised_days)                              AS avg_promised_days
FROM fct_orders f
WHERE f.is_delivered AND f.purchased_at >= ? AND f.purchased_at < ? {extra}
GROUP BY 1
ORDER BY 1"""


def weekly(con, start: dt.date, end: dt.date, filters: dict | None = None) -> Result:
    """Weekly late rate, delivery time and promise between two dates (by purchase
    week). Useful when an episode straddles a month boundary."""
    where, wparams = _where(filters, None)
    # Drop the complete-month clause: weeks are bounded by start/end instead.
    extra_clauses = where.split(" AND ")[1:]
    extra = "".join(f" AND {c}" for c in extra_clauses)
    sql = WEEKLY_SQL.format(extra=extra)
    params = [start, end] + wparams
    return Result(con.execute(sql, params).df(), sql, params)


# --- Slower deliveries or shorter promises? ------------------------------------

PROMISE_SQL = """
WITH base_promise AS (
    -- The base month's typical promise per destination state.
    SELECT f.customer_state, median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND {base_where}
    GROUP BY 1
), overall AS (
    SELECT median(f.promised_days) AS yardstick_days
    FROM fct_orders f
    WHERE f.is_delivered AND {base_where}
)
SELECT
    CASE WHEN f.purchase_month = ? THEN 'base' ELSE 'current' END AS period,
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
WHERE f.is_delivered AND {where}
GROUP BY 1
ORDER BY 1"""


def promise_vs_speed(con, base: dt.date, current: dt.date, filters: dict | None = None) -> Result:
    """Split a change in the late rate into 'deliveries got slower' and 'promises got shorter'.

    An order is late when delivery_days > promised_days, so the rate can move
    for either reason. Both months are re-scored against the SAME yardstick:
    the base month's median promise for each destination state.

        delivery_effect = change in late rate against that fixed yardstick
        promise_effect  = the rest of the change (from promises moving)

    The two add up exactly to the actual change."""
    base_where, base_params = _where(filters, [base])
    where, params = _where(filters, [base, current])
    sql = PROMISE_SQL.format(base_where=base_where, where=where)
    all_params = base_params + base_params + [base] + params
    df = con.execute(sql, all_params).df()
    if set(df.period) != {"base", "current"}:
        raise ValueError("no delivered orders in one of the two months for these filters")
    b = df[df.period == "base"].iloc[0]
    c = df[df.period == "current"].iloc[0]
    delta = 100 * (c.late_rate - b.late_rate)
    delivery = 100 * (c.late_rate_same_yardstick - b.late_rate_same_yardstick)
    notes = {"base": base, "current": current, "base_rate": float(b.late_rate), "current_rate": float(c.late_rate),
             "delta_pp": float(delta), "delivery_effect_pp": float(delivery), "promise_effect_pp": float(delta - delivery)}
    return Result(df, sql, all_params, notes)


# --- Data quality --------------------------------------------------------------

DQ_SQL = """
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
WHERE m.purchase_month IN ({months})
GROUP BY ALL
ORDER BY m.purchase_month"""


def data_quality(con, months: list[dt.date]) -> Result:
    sql = DQ_SQL.format(months=", ".join("?" for _ in months))
    return Result(con.execute(sql, list(months)).df(), sql, list(months))
