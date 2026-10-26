"""Agent, tools and citation checker, tested with a scripted model (no LLM needed)."""

import json

import pytest

from src.config import DB_PATH, ROOT
from src.report import check_report

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="database not built")


# --- Citation checker (pure) ----------------------------------------------------

EV = {"E1": {"rate_pct": 19.0, "base": {"rate_pct": 14.1}, "change_pp": 4.84, "out_of": 7003, "flagged": ["MG", "RS"]},
      "E2": {"mix_pp": -0.4, "performance_pp": 5.33}}


def statuses(text):
    return [(n.text, n.status) for n in check_report(text, EV).numbers]


def test_supported_numbers_at_shown_precision():
    assert statuses("The rate rose from 14.1% to 19.0%, up 4.8 pp, across 7,003 orders [E1].") == [
        ("14.1", "supported"), ("19.0", "supported"), ("4.8", "supported"), ("7,003", "supported")]


def test_sign_is_ignored_and_list_lengths_count():
    assert statuses("Mix fell by 0.4 pp [E2]. 2 states were flagged [E1].") == [("0.4", "supported"), ("2", "supported")]


def test_uncited_miscited_unsupported():
    s = dict(statuses("The rate hit 19.0%.\n\nPerformance added 5.33 pp [E1]. Savings could reach 40% [E1]."))
    assert s == {"19.0": "uncited", "5.33": "miscited", "40": "unsupported"}


def test_dates_ids_and_numbering_are_ignored():
    text = "1. In 2018-03 (week of 26 February 2018) order 7f3a9c21ab rose to 19.0% [E1]."
    assert statuses(text) == [("19.0", "supported")]


def test_unknown_citation_is_reported():
    r = check_report("The rate was 19.0% [E9].", EV)
    assert r.unknown_citations == ["E9"] and not r.ok


# --- Tools ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def con():
    from src.db import connect
    c = connect()
    yield c
    c.close()


@needs_db
def test_tools_reject_bad_arguments(con):
    from src.tools import ToolBox
    tb = ToolBox(con)
    bad = [("compare_periods", {"metric": "late_rate", "base_month": "March", "current_month": "2018-03"}),
           ("compare_periods", {"metric": "revenue", "base_month": "2018-02", "current_month": "2018-03"}),
           ("break_down_metric", {"metric": "late_rate", "dimension": "x; DROP TABLE fct_orders", "base_month": "2018-02", "current_month": "2018-03"}),
           ("compare_periods", {"metric": "late_rate", "base_month": "2018-02", "current_month": "2018-03", "filters": {"customer_state": "ZZ"}}),
           ("delete_everything", {})]
    for name, args in bad:
        ev = tb.call(name, args)
        assert not ev.ok and "error" in ev.output and ev.id is None
    # Failed calls don't consume evidence ids.
    assert tb.call("check_data_quality", {"months": ["2018-03"]}).id == "E1"


@needs_db
def test_tools_gate_insufficient_evidence(con):
    from src.tools import ToolBox
    tb = ToolBox(con)
    for name, args in [("compare_periods", {"metric": "late_rate", "base_month": "2016-09", "current_month": "2016-10"}),
                       ("break_down_metric", {"metric": "late_rate", "dimension": "customer_state", "base_month": "2016-09", "current_month": "2016-10"}),
                       ("compare_periods", {"metric": "late_rate", "base_month": "2018-02", "current_month": "2018-03", "filters": {"customer_state": "AC"}})]:
        out = tb.call(name, args).output
        assert out["insufficient_evidence"] is True
        assert "change_pp" not in out and "decomposition" not in out


@needs_db
def test_tool_numbers_match_metrics_layer(con):
    from src.tools import ToolBox
    out = ToolBox(con).call("compare_periods", {"metric": "late_rate", "base_month": "2018-02", "current_month": "2018-03"}).output
    assert (out["base"]["rate_pct"], out["current"]["rate_pct"], out["change_pp"]) == (14.1, 19.0, 4.84)
    v = out["delivery_vs_promise"]
    assert v["delivery_effect_pp"] + v["promise_effect_pp"] == pytest.approx(out["change_pp"], abs=0.011)


# --- Agent loop with a scripted model --------------------------------------------

def _resp(text="", calls=()):
    return {"text": text, "tool_calls": [{"name": n, "arguments": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)]}


FEB, MAR = "2018-02", "2018-03"
DQ = ("check_data_quality", {"months": [FEB, MAR]})
CMP = ("compare_periods", {"metric": "late_rate", "base_month": FEB, "current_month": MAR})
BD = lambda d: ("break_down_metric", {"metric": "late_rate", "dimension": d, "base_month": FEB, "current_month": MAR})
GOOD = "## Answer\nThe late rate rose from 14.1% to 19.0% [E2]; mix was 0.22 pp [E3] and performance 5.33 pp [E4]."


def run(con, responses):
    from src.agent import investigate
    from src.llm import ReplayProvider
    return investigate(ReplayProvider(responses), FEB, MAR, con=con)


@needs_db
def test_happy_path(con):
    rec = run(con, [_resp(calls=[DQ]), _resp(calls=[CMP]), _resp(calls=[BD("seller_state"), BD("customer_state")]), _resp(GOOD)])
    assert rec.stopped_reason == "report" and rec.guard_events == []
    assert rec.check["summary"]["support_rate"] == 1.0
    assert [e["id"] for e in rec.evidence] == ["E1", "E2", "E3", "E4"]


@needs_db
def test_report_before_required_steps_is_sent_back(con):
    rec = run(con, [_resp(GOOD), _resp(calls=[DQ, CMP, BD("seller_state"), BD("customer_state")]), _resp(GOOD)])
    assert any("required steps" in g for g in rec.guard_events)
    assert rec.transcript[0]["type"] == "guard" and rec.stopped_reason == "report"


@needs_db
def test_unsupported_number_triggers_one_repair(con):
    bad = GOOD + " This will save R$ 50,000 [E2]."
    rec = run(con, [_resp(calls=[DQ, CMP, BD("seller_state"), BD("customer_state")]), _resp(bad), _resp(GOOD)])
    assert any("citation check failed" in g for g in rec.guard_events)
    assert rec.report == GOOD and rec.check["summary"]["unsupported"] == 0


@needs_db
def test_failed_repair_keeps_better_draft(con):
    bad = GOOD + " This will save R$ 50,000 [E2]."
    worse = "Savings of 99% and 12,345 orders [E1]."
    rec = run(con, [_resp(calls=[DQ, CMP, BD("seller_state"), BD("customer_state")]), _resp(bad), _resp(worse)])
    assert rec.report == bad and "kept the first draft" in rec.guard_events[-1]


@needs_db
def test_repeated_tool_errors_stop_the_run(con):
    junk = ("compare_periods", {"metric": "nope", "base_month": FEB, "current_month": MAR})
    rec = run(con, [_resp(calls=[junk]), _resp(calls=[junk]), _resp(calls=[junk]), _resp(GOOD)])
    assert rec.stopped_reason == "tool_errors" and rec.n_tool_errors == 3
    assert "stopped before a report" in rec.report


@needs_db
def test_incomplete_months_need_only_data_quality(con):
    from src.agent import investigate
    from src.llm import ReplayProvider
    report = "## Answer\nInsufficient evidence: 2016-09 has only 4 orders [E1]."
    rec = investigate(ReplayProvider([_resp(calls=[("check_data_quality", {"months": ["2016-09", "2016-10"]})]), _resp(report)]),
                      "2016-09", "2016-10", con=con)
    assert rec.guard_events == [] and rec.check["summary"]["support_rate"] == 1.0


@needs_db
@pytest.mark.parametrize("path", sorted((ROOT / "replays").glob("*.json")), ids=lambda p: p.stem)
def test_saved_replays_reproduce_exactly(con, path):
    """Replaying a saved run re-executes every tool against the database; the
    evidence and the report must come out identical."""
    from src.agent import RunRecord, replay
    saved = RunRecord.load(path)
    rec = replay(path, con)
    assert rec.report == saved.report
    assert json.dumps([e["output"] for e in rec.evidence], default=str) == json.dumps([e["output"] for e in saved.evidence], default=str)
