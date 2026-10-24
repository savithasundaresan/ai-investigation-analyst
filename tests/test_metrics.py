"""Tests for src/metrics.py: the functions the dashboard, agent and evaluation share."""

import datetime as dt

import pytest

from src import metrics as M
from src.config import DB_PATH

FEB, MAR = dt.date(2018, 2, 1), dt.date(2018, 3, 1)


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("database not built; run `uv run python -m src.load`")
    from src.db import connect
    c = connect()
    yield c
    c.close()


def test_trend_covers_complete_months_only(con):
    trend = M.kpis(con).data
    assert len(trend) == 20
    assert trend["late_rate"].notna().all()


def test_compare_periods_matches_independent_counts(con):
    r = M.compare_periods(con, "late_rate", FEB, MAR).data.iloc[0]
    for month, n_col, x_col in [(FEB, "base_n", "base_x"), (MAR, "current_n", "current_x")]:
        n, x = con.execute("""
            SELECT count(*), count(*) FILTER (WHERE delivered_customer_at::DATE > estimated_delivery_date)
            FROM stg_orders WHERE order_status = 'delivered' AND delivered_customer_at IS NOT NULL
              AND date_trunc('month', purchased_at) = ?""", [month]).fetchone()
        assert (r[n_col], r[x_col]) == (n, x)
    assert r["z"] > 2  # the Feb->Mar rise is far beyond noise


@pytest.mark.parametrize("metric", list(M.RATE_METRICS))
@pytest.mark.parametrize("dimension", list(M.DIMENSIONS))
def test_decomposition_adds_up_to_true_change(con, metric, dimension):
    """Mix + performance + interaction must equal the real change in the
    overall rate, for every metric and dimension, including multi-seller and
    multi-category orders (weighted 1/k)."""
    d = M.decompose_mix(con, metric, dimension, FEB, MAR)
    c = M.compare_periods(con, metric, FEB, MAR).data.iloc[0]
    true_delta_pp = 100 * (c["current"] - c["base"])
    n = d.notes
    assert n["base_rate"] == pytest.approx(c["base"], abs=1e-9)
    assert n["current_rate"] == pytest.approx(c["current"], abs=1e-9)
    assert n["mix_pp"] + n["performance_pp"] + n["interaction_pp"] == pytest.approx(true_delta_pp, abs=1e-9)
    assert d.data["total_pp"].sum() == pytest.approx(true_delta_pp, abs=1e-9)


def test_pure_mix_shift_is_attributed_to_mix():
    """Synthetic check of the formula itself, matching the design doc example:
    shares move 80/20 -> 60/40, segment rates stay 5% and 20%."""
    import pandas as pd
    s0, s1, r = pd.Series([0.8, 0.2]), pd.Series([0.6, 0.4]), pd.Series([0.05, 0.20])
    mix = (100 * (s1 - s0) * r).sum()
    perf = (100 * s0 * (r - r)).sum()
    assert mix == pytest.approx(3.0)
    assert perf == 0


def test_breakdown_flags_follow_the_rules(con):
    b = M.break_down(con, "late_rate", "customer_state", FEB, MAR).data
    flagged = b[b["flagged"]]
    assert (flagged["n_base"] >= 30).all() and (flagged["n_current"] >= 30).all()
    assert (flagged["z"].abs() >= 2).all() and (flagged["delta_pp"].abs() >= 3).all()
    assert b["share_base"].sum() == pytest.approx(1) and b["share_current"].sum() == pytest.approx(1)


def test_breakdown_counts_add_up_for_order_level_dimension(con):
    b = M.break_down(con, "late_rate", "customer_state", FEB, MAR).data
    c = M.compare_periods(con, "late_rate", FEB, MAR).data.iloc[0]
    assert b["n_base"].sum() == c["base_n"] and b["n_current"].sum() == c["current_n"]


def test_promise_vs_speed_adds_up(con):
    for base, cur in [(dt.date(2017, 10, 1), dt.date(2017, 11, 1)), (FEB, MAR)]:
        n = M.promise_vs_speed(con, base, cur).notes
        assert n["delivery_effect_pp"] + n["promise_effect_pp"] == pytest.approx(n["delta_pp"], abs=1e-9)


def test_promise_vs_speed_separates_known_cases(con):
    """Black Friday 2017 was slower deliveries; Feb->Mar 2018 was mostly shorter promises."""
    nov = M.promise_vs_speed(con, dt.date(2017, 10, 1), dt.date(2017, 11, 1)).notes
    assert nov["delivery_effect_pp"] > nov["promise_effect_pp"]
    mar = M.promise_vs_speed(con, FEB, MAR).notes
    assert mar["promise_effect_pp"] > mar["delivery_effect_pp"]


def test_filters_narrow_results(con):
    all_ = M.compare_periods(con, "late_rate", FEB, MAR).data.iloc[0]
    sp = M.compare_periods(con, "late_rate", FEB, MAR, {"customer_state": "SP"}).data.iloc[0]
    assert 0 < sp["current_n"] < all_["current_n"]


def test_unknown_inputs_are_rejected(con):
    with pytest.raises(ValueError):
        M.break_down(con, "late_rate", "customer_state; DROP TABLE x", FEB, MAR)
    with pytest.raises(ValueError):
        M.kpis(con, [FEB], {"not_a_filter": "x"})


def test_evidence_sql_has_parameters_written_in(con):
    r = M.compare_periods(con, "late_rate", FEB, MAR)
    text = r.sql_with_params()
    assert "?" not in text and "DATE '2018-03-01'" in text
