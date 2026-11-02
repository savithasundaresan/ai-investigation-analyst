"""The evaluation's answer key and scoring rules.

The answer key is a second implementation built from the staging tables. If it
agrees with the agent's tools on every question, both are very likely right;
if they disagree, one of them has a bug.
"""

import pytest

from src.answer_key import answer
from src.db import connect
from src.eval_questions import QUESTIONS
from src.evaluate import score_facts, traceable
from src.tools import ToolBox


@pytest.fixture(scope="module")
def con():
    return connect()


@pytest.fixture(scope="module")
def keys(con):
    return {q["id"]: answer(con, q["base"], q["current"]) for q in QUESTIONS}


def test_question_set_shape():
    assert 20 <= len(QUESTIONS) <= 30
    assert len({q["id"] for q in QUESTIONS}) == len(QUESTIONS)
    kinds = {q["kind"] for q in QUESTIONS}
    assert {"rise", "fall", "flat", "incomplete", "missing"} <= kinds


def test_completeness_matches_dim_month(con, keys):
    complete = {f"{r[0]:%Y-%m}" for r in con.execute("SELECT purchase_month FROM dim_month WHERE is_complete").fetchall()}
    for q in QUESTIONS:
        k = keys[q["id"]]
        assert k["base_complete"] == (q["base"] in complete), q
        assert k["current_complete"] == (q["current"] in complete), q


def test_labels_match_answer_key(keys):
    """The kind labels are only for grouping, but they should not contradict the key."""
    for q in QUESTIONS:
        k = keys[q["id"]]
        if q["kind"] in ("incomplete", "missing"):
            assert k["expect_insufficient"], q
            continue
        assert not k["expect_insufficient"], q
        if q["kind"] == "flat":
            assert not k["beyond_noise"], q
        else:
            assert k["beyond_noise"], q
            assert k["direction"] == ("down" if q["kind"] == "fall" else "up"), q


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q["kind"] not in ("incomplete", "missing")], ids=lambda q: q["id"])
def test_answer_key_agrees_with_tools(con, keys, q):
    k = keys[q["id"]]
    tb = ToolBox(con)
    cp = tb.call("compare_periods", {"metric": "late_rate", "base_month": q["base"], "current_month": q["current"]}).output
    assert cp["base"]["rate_pct"] == round(k["base_rate_pct"], 1)
    assert cp["current"]["rate_pct"] == round(k["current_rate_pct"], 1)
    assert cp["change_pp"] == pytest.approx(k["change_pp"], abs=0.006)
    assert cp["z"] == pytest.approx(k["z"], abs=0.006)
    assert cp["beyond_noise"] == k["beyond_noise"]
    dvp = cp["delivery_vs_promise"]
    assert dvp["delivery_effect_pp"] == pytest.approx(k["delivery_effect_pp"], abs=0.006)
    assert dvp["promise_effect_pp"] == pytest.approx(k["promise_effect_pp"], abs=0.006)
    for dim in ("customer_state", "seller_state"):
        bd = tb.call("break_down_metric", {"metric": "late_rate", "dimension": dim,
                                           "base_month": q["base"], "current_month": q["current"]}).output
        top = next(s for s in bd["top_contributors"] if s["segment"] != "Other")
        assert top["segment"] == k[f"top_{dim}"]["segment"], dim
        assert top["total_pp"] == pytest.approx(k[f"top_{dim}"]["contribution_pp"], abs=0.006), dim


def test_insufficient_questions_get_gated_by_tools(con, keys):
    """For every incomplete pair the tools refuse to give rates, as the key expects."""
    for q in QUESTIONS:
        if q["kind"] != "incomplete":
            continue
        out = ToolBox(con).call("compare_periods", {"metric": "late_rate", "base_month": q["base"],
                                                    "current_month": q["current"]}).output
        assert out.get("insufficient_evidence") or "error" in out, q


# --- Scoring rules ---------------------------------------------------------------

KEY = {"expect_insufficient": False, "base_rate_pct": 14.13, "current_rate_pct": 18.96, "change_pp": 4.84,
       "z": 7.56, "direction": "up", "beyond_noise": True, "delivery_effect_pp": 1.5, "promise_effect_pp": 3.34,
       "dominant_effect": "promise", "top_customer_state": {"segment": "MG"}, "top_seller_state": {"segment": "SP"}}


def facts(report, key=KEY):
    return {f["fact"]: f["ok"] for f in score_facts(report, key)}


def test_scoring_full_marks():
    r = ("## Answer\nThe late rate rose from 14.1% to 19.0% [E2], up 4.84 pp, mostly because promised delivery "
         "windows got shorter.\n## Findings\n- Delivery effect 1.5 pp, promise effect 3.34 pp [E2].\n"
         "- Minas Gerais contributed most; sellers in SP [E3].")
    assert all(facts(r).values()), facts(r)


def test_scoring_catches_both_and_missing_state():
    r = ("## Answer\nThe late rate rose from 14.1% to 19.0% (4.8 pp), driven by both slower deliveries and "
         "shorter promises. Delivery 1.5 pp, promise 3.34 pp. MG led.\n")
    f = facts(r)
    assert not f["names the larger effect"] and not f["top seller state"] and f["top customer state"]


def test_scoring_accepts_contrast_phrasing():
    key = KEY | {"dominant_effect": "delivery"}
    assert facts("This was driven by slower deliveries, not shorter promises.", key)["names the larger effect"]
    assert facts("Slower deliveries contributed more to the rise than shorter promises.", key)["names the larger effect"]
    assert not facts("Shorter promises, not slower deliveries, drove it.", key)["names the larger effect"]


def test_scoring_wrong_direction_and_noise():
    key = KEY | {"direction": "down", "beyond_noise": False, "change_pp": -0.21}
    f = facts("## Answer\nThe late rate increased from 4.4% to 4.2%.", key)
    assert not f["direction of the change"] and not f["noise verdict"]
    f = facts("## Answer\nThe rate fell slightly, from 4.4% to 4.2%; the change is not statistically significant.", key)
    assert f["direction of the change"] and f["noise verdict"]


def test_scoring_insufficient():
    key = {"expect_insufficient": True}
    assert all(facts("## Answer\nInsufficient evidence: 2016-09 is not a complete month [E1].", key).values())
    f = facts("## Answer\nThe rate rose from 0.8% to 100%.", key)
    assert not f["says insufficient evidence"] and not f["reports no late rates"]
    f = facts("## Answer\nInsufficient evidence: 2018-09 has 16 orders and 6.2% are still open [E1].", key)
    assert all(f.values()), f
    assert not facts("## Answer\nInsufficient evidence.", KEY)["does not wrongly abstain"]


def test_traceable_counts_made_up_numbers():
    assert traceable("The rate was 14.1% and 7 orders, savings of R$ 5,000.", [14.13, 7]) == (3, 2)
    assert traceable("Rose in 2018-03 [E2].", [1]) == (0, 0)
