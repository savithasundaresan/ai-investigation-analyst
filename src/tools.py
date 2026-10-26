"""The agent's four read-only tools (decision D8).

Each tool:
  1. validates its arguments against fixed lists (the model never writes SQL),
  2. calls src/metrics.py (the same functions the dashboard uses),
  3. returns compact JSON with numbers already computed and rounded, so the
     model only reads numbers and never does arithmetic,
  4. is logged as an evidence item E1, E2, ... with the exact SQL that ran.

A failed call returns {"error": ...} to the model and is logged as a tool failure.
"""

import datetime as dt
import math
import re
import time
from dataclasses import dataclass, field

from src import metrics as M
from src.config import MIN_ABS_Z, MIN_DELTA_PP, MIN_N

RATE_METRICS = list(M.RATE_METRICS)
ALL_METRICS = RATE_METRICS + list(M.VALUE_METRICS)
DIMENSIONS = list(M.DIMENSIONS)

_FILTERS_SCHEMA = {
    "type": "object",
    "description": "Optional. Restrict to orders matching all given values.",
    "properties": {
        "customer_state": {"type": "string", "description": "Two-letter customer state, e.g. 'SP'"},
        "seller_state": {"type": "string", "description": "Two-letter seller state, e.g. 'SP'"},
        "category": {"type": "string", "description": "Product category in English, e.g. 'health_beauty'"},
        "seller_id": {"type": "string", "description": "Seller id (32 hex characters)"},
    },
    "additionalProperties": False,
}
_MONTH = {"type": "string", "description": "Month as YYYY-MM, e.g. '2018-03'"}

TOOL_SCHEMAS = [
    {
        "name": "check_data_quality",
        "description": "Check whether months are complete and trustworthy before comparing them: order counts, "
                       "share of orders still open, estimates beyond the data cutoff, overdue undelivered orders "
                       "and timestamp anomalies. Call this first.",
        "parameters": {"type": "object", "properties": {"months": {"type": "array", "items": _MONTH, "minItems": 1, "maxItems": 4}},
                       "required": ["months"], "additionalProperties": False},
    },
    {
        "name": "compare_periods",
        "description": "Compare one metric between a base month and a current month: both values, the change, "
                       "and for rates a z-score (|z| >= 2 means beyond noise). For late_rate it also splits the "
                       "change into slower deliveries vs shorter delivery promises.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ALL_METRICS},
            "base_month": _MONTH, "current_month": _MONTH, "filters": _FILTERS_SCHEMA},
            "required": ["metric", "base_month", "current_month"], "additionalProperties": False},
    },
    {
        "name": "break_down_metric",
        "description": "Break a rate's change down by one dimension. Returns the mix vs performance decomposition "
                       "(mix = orders shifted toward segments that were already worse; performance = segments got "
                       "worse), the top contributing segments, and segments flagged by the anomaly rules.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": RATE_METRICS},
            "dimension": {"type": "string", "enum": DIMENSIONS},
            "base_month": _MONTH, "current_month": _MONTH, "filters": _FILTERS_SCHEMA},
            "required": ["metric", "dimension", "base_month", "current_month"], "additionalProperties": False},
    },
    {
        "name": "inspect_orders",
        "description": "Look at concrete delivered orders in one month (optionally only late ones), with a summary "
                       "of promised vs actual delivery days and a weekly profile of the month.",
        "parameters": {"type": "object", "properties": {
            "month": _MONTH, "filters": _FILTERS_SCHEMA,
            "late_only": {"type": "boolean", "description": "Default true"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 8"}},
            "required": ["month"], "additionalProperties": False},
    },
]


class ToolError(ValueError):
    pass


@dataclass
class Evidence:
    id: str | None
    tool: str
    args: dict
    output: dict
    queries: list[str] = field(default_factory=list)
    ok: bool = True
    duration_s: float = 0.0


# --- Helpers ---------------------------------------------------------------------------

def _r(x, d=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), d)


def _pct(x, d=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(100 * float(x), d)


class ToolBox:
    def __init__(self, con):
        self.con = con
        self.evidence: list[Evidence] = []
        self.complete = set(M.complete_months(con))
        self.all_months = {r[0] for r in con.execute("SELECT purchase_month FROM dim_month").fetchall()}

    # --- validation -----------------------------------------------------------------
    def _month(self, s) -> dt.date:
        if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}", s):
            raise ToolError(f"month must look like '2018-03', got {s!r}")
        d = dt.date(int(s[:4]), int(s[5:]), 1)
        if d not in self.all_months:
            first, last = min(self.all_months), max(self.all_months)
            raise ToolError(f"no orders in {s}; data covers {first:%Y-%m} to {last:%Y-%m}")
        return d

    def _filters(self, f) -> dict:
        if not f:
            return {}
        if not isinstance(f, dict):
            raise ToolError("filters must be an object")
        out = {}
        for k, v in f.items():
            if v in (None, ""):
                continue
            if k not in M.FILTER_KEYS:
                raise ToolError(f"unknown filter {k!r}; allowed: {list(M.FILTER_KEYS)}")
            table = "fct_orders" if k == "customer_state" else "bridge_order_segment"
            v = str(v).upper() if k.endswith("state") else str(v)
            if not self.con.execute(f"SELECT 1 FROM {table} WHERE {k} = ? LIMIT 1", [v]).fetchone():
                raise ToolError(f"no orders with {k} = {v!r}")
            out[k] = v
        return out

    def _enum(self, value, allowed, name):
        if value not in allowed:
            raise ToolError(f"{name} must be one of {allowed}, got {value!r}")
        return value

    def _warnings(self, months: list[dt.date]) -> list[str]:
        return [f"{m:%Y-%m} is outside the complete-month window ({min(self.complete):%Y-%m} to "
                f"{max(self.complete):%Y-%m}); treat its numbers as unreliable" for m in months if m not in self.complete]

    def _insufficient(self, months: list[dt.date], counts: dict[str, int], unit: str) -> dict | None:
        """Hard gate: no rates or decompositions for incomplete months or tiny samples."""
        reasons = self._warnings(months)
        small = {k: v for k, v in counts.items() if v < MIN_N}
        if small:
            reasons.append(f"fewer than {MIN_N} {unit} in " + ", ".join(f"{k} ({v})" for k, v in small.items()))
        if not reasons:
            return None
        return {"insufficient_evidence": True, "reasons": reasons, "counts": counts,
                "instruction": "Do not report rates, changes or causes for this comparison. Tell the user the "
                               "evidence is insufficient and why, and suggest comparing complete months instead."}

    # --- dispatch ---------------------------------------------------------------------
    def call(self, name: str, args: dict) -> Evidence:
        t0 = time.perf_counter()
        fn = {"check_data_quality": self.check_data_quality, "compare_periods": self.compare_periods,
              "break_down_metric": self.break_down_metric, "inspect_orders": self.inspect_orders}.get(name)
        try:
            if fn is None:
                raise ToolError(f"unknown tool {name!r}")
            output, results = fn(**(args or {}))
            ev = Evidence(id=f"E{sum(e.ok for e in self.evidence) + 1}", tool=name, args=args, output=output,
                          queries=[r.sql_with_params().strip() for r in results])
            ev.output = {"evidence_id": ev.id, **output}
        except (ToolError, TypeError, ValueError) as e:
            ev = Evidence(id=None, tool=name, args=args, output={"error": str(e)}, ok=False)
        ev.duration_s = time.perf_counter() - t0
        self.evidence.append(ev)
        return ev

    # --- tools ------------------------------------------------------------------------
    def check_data_quality(self, months):
        if not isinstance(months, list) or not months:
            raise ToolError("months must be a non-empty list like ['2018-02', '2018-03']")
        ms = [self._month(m) for m in months[:4]]
        r = M.data_quality(self.con, ms)
        rows = []
        for x in r.data.itertuples(index=False):
            rows.append({
                "month": f"{x.purchase_month:%Y-%m}", "complete": bool(x.is_complete),
                "orders": int(x.n_orders), "delivered": int(x.n_delivered),
                "open_orders_pct": _pct(x.share_open), "estimates_after_data_cutoff_pct": _pct(x.share_estimate_after_cutoff),
                "overdue_undelivered": int(x.overdue_undelivered),
                "timestamp_anomalies": int(x.delivered_without_date + x.carrier_before_purchase + x.delivered_before_carrier),
                "orders_without_items": int(x.no_items), "payment_mismatches": int(x.payment_mismatch),
            })
        return {"months": rows,
                "complete_month_window": f"{min(self.complete):%Y-%m} to {max(self.complete):%Y-%m}",
                "completeness_rule": {"min_orders": 500, "max_open_orders_pct": 5, "max_estimates_after_cutoff_pct": 1}}, [r]

    def compare_periods(self, metric, base_month, current_month, filters=None):
        metric = self._enum(metric, ALL_METRICS, "metric")
        b, c = self._month(base_month), self._month(current_month)
        f = self._filters(filters)
        r = M.compare_periods(self.con, metric, b, c, f)
        row = r.data.iloc[0]
        out = {"metric": metric, "filters": f, "base_month": base_month, "current_month": current_month}
        warnings = self._warnings([b, c])
        results = [r]
        if metric in RATE_METRICS:
            den = {"late_rate": "delivered orders", "low_review_rate": "reviewed orders", "cancellation_rate": "orders"}[metric]
            if row.get("base_n") is None or (isinstance(row.get("base_n"), float) and math.isnan(row["base_n"])):
                raise ToolError("no orders match these filters in one of the months")
            gate = self._insufficient([b, c], {base_month: int(row["base_n"]), current_month: int(row["current_n"])}, den)
            if gate:
                return out | gate, [r]
            out |= {
                "base": {"rate_pct": _pct(row["base"]), "count": int(row["base_x"]), "out_of": int(row["base_n"]), "denominator": den},
                "current": {"rate_pct": _pct(row["current"]), "count": int(row["current_x"]), "out_of": int(row["current_n"]), "denominator": den},
                "change_pp": _r(row["delta_pp"], 2), "z": _r(row["z"], 2),
                # A z-test means nothing on a handful of orders.
                "beyond_noise": bool(abs(row["z"]) >= MIN_ABS_Z and min(row["base_n"], row["current_n"]) >= MIN_N),
            }
            if metric == "late_rate" and min(row["base_n"], row["current_n"]) > 0:
                p = M.promise_vs_speed(self.con, b, c, f)
                pd_ = p.data.set_index("period")
                out["delivery_vs_promise"] = {
                    "delivery_effect_pp": _r(p.notes["delivery_effect_pp"], 2),
                    "promise_effect_pp": _r(p.notes["promise_effect_pp"], 2),
                    "explanation": "delivery_effect = change in late rate if both months were judged against the base "
                                   "month's median promise per customer state; promise_effect = the rest, caused by "
                                   "promised delivery windows changing. They add up to change_pp.",
                    "median_promised_days": {"base": _r(pd_.loc["base", "median_promised_days"]), "current": _r(pd_.loc["current", "median_promised_days"])},
                    "median_delivery_days": {"base": _r(pd_.loc["base", "median_delivery_days"]), "current": _r(pd_.loc["current", "median_delivery_days"])},
                    "avg_promised_days": {"base": _r(pd_.loc["base", "avg_promised_days"]), "current": _r(pd_.loc["current", "avg_promised_days"])},
                    "avg_delivery_days": {"base": _r(pd_.loc["base", "avg_delivery_days"]), "current": _r(pd_.loc["current", "avg_delivery_days"])},
                }
                results.append(p)
        else:
            d = 2 if metric == "aov" else 1
            out |= {"base": _r(row["base"], d), "current": _r(row["current"], d),
                    "change": _r(row.get("delta"), d)}
        out["warnings"] = warnings
        return out, results

    def break_down_metric(self, metric, dimension, base_month, current_month, filters=None):
        metric = self._enum(metric, RATE_METRICS, "metric")
        dimension = self._enum(dimension, DIMENSIONS, "dimension")
        b, c = self._month(base_month), self._month(current_month)
        f = self._filters(filters)
        d = M.decompose_mix(self.con, metric, dimension, b, c, f)
        bd = M.break_down(self.con, metric, dimension, b, c, f)
        n = d.notes
        gate = self._insufficient([b, c], {base_month: int(d.data.n_base.sum()), current_month: int(d.data.n_current.sum())},
                                  "orders in the denominator")
        if gate:
            return {"metric": metric, "dimension": dimension, "filters": f, "base_month": base_month,
                    "current_month": current_month} | gate, [d]
        top = [{"segment": x.segment, "n_base": int(x.n_base), "n_current": int(x.n_current),
                "share_base_pct": _pct(x.share_base), "share_current_pct": _pct(x.share_current),
                "rate_base_pct": _pct(x.rate_base), "rate_current_pct": _pct(x.rate_current),
                "mix_pp": _r(x.mix_pp, 2), "performance_pp": _r(x.performance_pp, 2), "total_pp": _r(x.total_pp, 2)}
               for x in d.data.head(6).itertuples(index=False)]
        flagged = bd.data[bd.data.flagged].sort_values("n_current", ascending=False).head(8)
        flags = [{"segment": x.segment, "n_base": int(x.n_base), "n_current": int(x.n_current),
                  "rate_base_pct": _pct(x.rate_base), "rate_current_pct": _pct(x.rate_current),
                  "change_pp": _r(x.delta_pp, 2), "z": _r(x.z, 2)} for x in flagged.itertuples(index=False)]
        out = {
            "metric": metric, "dimension": dimension, "filters": f, "base_month": base_month, "current_month": current_month,
            "overall": {"base_pct": _pct(n["base_rate"]), "current_pct": _pct(n["current_rate"]), "change_pp": _r(n["delta_pp"], 2)},
            "decomposition": {"mix_pp": _r(n["mix_pp"], 2), "performance_pp": _r(n["performance_pp"], 2),
                              "interaction_pp": _r(n["interaction_pp"], 2)},
            "segments_total": n["n_segments"],
            "segments_with_enough_orders": int(bd.data.meets_min_n.sum()),
            "segments_pooled_into_other": n["n_pooled_into_other"],
            "top_contributors": top,
            "flagged_segments": flags,
            "flag_rules": {"min_orders_each_month": MIN_N, "min_abs_z": MIN_ABS_Z, "min_change_pp": MIN_DELTA_PP},
            "warnings": self._warnings([b, c]),
        }
        return out, [d, bd]

    def inspect_orders(self, month, filters=None, late_only=True, limit=8):
        m = self._month(month)
        f = self._filters(filters)
        limit = max(1, min(int(limit or 8), 20))
        rows, summary = M.sample_orders(self.con, m, f, bool(late_only), limit)
        nxt = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        wk = M.weekly(self.con, m, nxt, f)
        s = summary.data.iloc[0]
        out = {
            "month": month, "filters": f, "late_only": bool(late_only),
            "summary": {"delivered": int(s.delivered), "late": int(s.late),
                        "median_promised_days": _r(s.median_promised_days), "median_delivery_days": _r(s.median_delivery_days),
                        "median_days_late_of_late_orders": _r(s.median_days_late)},
            "weekly": [{"week_of": f"{w.week:%Y-%m-%d}", "delivered": int(w.delivered), "late_rate_pct": _pct(w.late_rate),
                        "avg_promised_days": _r(w.avg_promised_days), "avg_delivery_days": _r(w.avg_delivery_days)}
                       for w in wk.data.itertuples(index=False)],
            "orders": [{"order_id": o.order_id[:8], "purchase_date": f"{o.purchase_date:%Y-%m-%d}", "customer_state": o.customer_state,
                        "seller_states": o.seller_states, "categories": o.categories, "promised_days": int(o.promised_days),
                        "delivery_days": int(o.delivery_days), "days_late": int(o.days_late),
                        "review_score": None if o.review_score is None or o.review_score != o.review_score else int(o.review_score)}
                       for o in rows.data.itertuples(index=False)],
            "warnings": self._warnings([m]),
        }
        return out, [summary, wk, rows]
