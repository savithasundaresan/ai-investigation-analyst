"""Evaluation: the agent vs a plain AI summary, on 25 questions with known answers.

    uv run python -m src.evaluate run              # run both systems (skips saved runs; ~1 hour locally)
    uv run python -m src.evaluate run --ids q01,q10 --force
    uv run python -m src.evaluate score            # score saved runs; no model needed

Two systems answer the same questions with the same model (qwen3:8b):
    agent     the Stage 3 agent: four read-only tools, evidence log, guardrails
    baseline  the model given the dashboard's KPI tiles for both months (plus
              whether each month is complete), no tools: a "basic AI summary"

Scoring is deterministic and written down here, so anyone can rerun it:
    accuracy   facts from the independent answer key (src/answer_key.py), each
               checked in the report text by a rule below
    evidence   share of the report's numbers that trace back to data the system
               actually saw (agent: also whether each number cites the right evidence)
    failures   tool calls that errored, runs that ended without a report
    latency    wall-clock seconds per question; cost is $0 (local model)

Text rules are strict on purpose: a fact counts only when the report states it
plainly. Every check is listed per question in docs/evaluation.md for audit.
"""

import argparse
import datetime as dt
import json
import re
import statistics
import time
from pathlib import Path

from src import metrics as M
from src.agent import RunRecord, investigate
from src.answer_key import answer
from src.config import MIN_N, ROOT
from src.db import connect
from src.eval_questions import QUESTIONS, question_text
from src.llm import OllamaProvider
from src.report import CITE_RE, NUM_RE, _matches, _scrub

EVAL_DIR = ROOT / "eval"
RUNS = {"agent": EVAL_DIR / "runs" / "agent", "baseline": EVAL_DIR / "runs" / "baseline"}
RESULTS_PATH = EVAL_DIR / "results.json"
DOC_PATH = ROOT / "docs" / "evaluation.md"

# --- Baseline: the model summarizing the dashboard, no tools ------------------------

BASELINE_SYSTEM = """You are a data analyst at Olist, a Brazilian e-commerce marketplace. The data is historical \
(2016-2018). You are given the dashboard's monthly KPI numbers for two months. Answer the user's question using only \
these numbers. Never invent numbers. Describe associations, not causes. If the data is not reliable enough to \
answer, start the Answer with "Insufficient evidence" and explain why.

Write the report in this format:
## Answer
Two or three sentences.
## Findings
Bullet points with numbers.
## Investigate first
One to three concrete things to look into, most important first.
## Limitations
Bullet points."""


def dashboard_text(con, base: str, current: str) -> tuple[str, list[float]]:
    """What the dashboard shows for the two months, as text, plus every number in it:
    each month's KPI tiles, the change the tiles display, completeness, and the
    small-sample warning."""
    lines, values, ks = [], [], {}
    status = {f"{r[0]:%Y-%m}": r[1] for r in con.execute("SELECT purchase_month, is_complete FROM dim_month").fetchall()}
    for label, month in (("Base month", base), ("Current month", current)):
        if month not in status:
            lines.append(f"{label} {month}: no orders in the data for this month.")
            continue
        d = dt.date(int(month[:4]), int(month[5:]), 1)
        k = ks[month] = M.kpis(con, [d]).data.iloc[0]
        row = {
            "complete month (enough orders, few still open)": "yes" if status[month] else "no",
            "orders": int(k.orders), "delivered orders": int(k.delivered),
            "late-delivery rate %": round(100 * k.late_rate, 2) if k.delivered else None,
            "overdue undelivered orders": int(k.overdue_undelivered),
            "average delivery time (days)": round(k.avg_delivery_days, 1) if k.delivered else None,
            "average promised delivery window (days)": round(k.avg_promised_days, 1) if k.delivered else None,
            "average order value (R$)": round(float(k.aov), 2),
            "low-review rate %": round(100 * k.low_review_rate, 2) if k.reviewed else None,
            "cancellation rate %": round(100 * k.cancellation_rate, 2),
        }
        lines.append(f"{label} {month}: " + "; ".join(f"{a} = {'n/a' if v is None else v}" for a, v in row.items()))
        values += [float(v) for v in row.values() if isinstance(v, (int, float))]
    if len(ks) == 2:
        b, c = ks[base], ks[current]
        if b.delivered and c.delivered:
            ch = {"late-delivery rate (pp)": round(100 * (c.late_rate - b.late_rate), 1),
                  "average delivery time (days)": round(c.avg_delivery_days - b.avg_delivery_days, 1),
                  "average promised window (days)": round(c.avg_promised_days - b.avg_promised_days, 1)}
        else:
            ch = {}
        ch |= {"orders": int(c.orders - b.orders), "cancellation rate (pp)": round(100 * (c.cancellation_rate - b.cancellation_rate), 1)}
        lines.append("Change shown on the tiles: " + "; ".join(f"{a} {v:+}" for a, v in ch.items()))
        values += [float(v) for v in ch.values()]
        if min(b.delivered, c.delivered) < MIN_N:
            lines.append(f"Dashboard warning: fewer than {MIN_N} delivered orders in one month; rates are too noisy to act on.")
            values.append(float(MIN_N))
    return "\n".join(lines), values


def run_baseline(provider, con, q: dict) -> dict:
    text, values = dashboard_text(con, q["base"], q["current"])
    prompt = f"{question_text(q)}\n\nDashboard numbers:\n{text}"
    t0 = time.perf_counter()
    resp = provider.chat(BASELINE_SYSTEM, [{"role": "user", "content": prompt}], [])
    report = re.sub(r"<think>.*?</think>", "", resp.text or "", flags=re.S).strip()
    return {"id": q["id"], "model": provider.model, "prompt": prompt, "input_values": values, "report": report,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
            "latency_s": round(time.perf_counter() - t0, 2)}


# --- Scoring rules ------------------------------------------------------------------

STATES = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
          "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
          "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
          "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
          "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
          "SE": "Sergipe", "TO": "Tocantins"}

INSUFFICIENT_RE = re.compile(r"insufficient (evidence|data)", re.I)
UP_RE = re.compile(r"\b(increas\w*|rose|rise[sn]?|rising|higher|jump\w*|grew|climb\w*|worsen\w*|went up)\b", re.I)
DOWN_RE = re.compile(r"\b(decreas\w*|fell|fall\w*|drop\w*|declin\w*|lower\w*|improv\w*|went down|did not (increase|rise))\b", re.I)
NOT_SIGNIFICANT_RE = re.compile(
    r"not (statistically )?significant|not beyond (the )?noise|within (the )?(range of )?(normal |random )?(noise|variation)"
    r"|(is|was) not beyond|random (variation|fluctuation)|(no|not a) (meaningful|significant|real|material) (change|increase|difference)"
    r"|(consistent with|indistinguishable from|likely|just) (random )?noise|negligible|essentially (flat|unchanged|stable)"
    r"|(remained|stayed|was) (roughly |essentially |largely )?(flat|stable|unchanged)", re.I)
PCT_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")
PP_NUM_RE = re.compile(r"(?<![\w.])[-+−]?(\d+(?:\.\d+)?)\s*(?:pp\b|percentage[- ]points?|points?\b|%)", re.I)
EFFECT_KW = {"promise": r"(promis\w*|delivery windows?|estimated? (dates?|windows?))",
             "delivery": r"(slower|faster|delivery[_ ]effect|delivery (times?|speed)|(took|taking) longer|deliveries (got|became|were) (slower|faster))"}
CONTRAST = r"(rather than|not|more than|instead of|outweigh\w*|than)"
DOMINANCE = r"(mostly|mainly|primarily|largely|majority|bulk|most of|chiefly|predominantly|larger|largest|bigger|biggest|main|dominant|primary)"


def _section(report: str, name: str) -> str:
    m = re.search(rf"##\s*{name}\s*\n(.*?)(?=\n##|\Z)", report or "", re.S | re.I)
    return m.group(1) if m else (report or "")[:500]


def _has_value(pattern: re.Pattern, text: str, target: float) -> bool:
    for m in pattern.finditer(text):
        raw = m.group(1)
        if _matches(float(raw), len(raw.split(".")[1]) if "." in raw else 0, {abs(target)}):
            return True
    return False


def _names_state(text: str, code: str) -> bool:
    return bool(re.search(rf"\b{code}\b", text) or re.search(re.escape(STATES.get(code, code)), text, re.I))


def _names_dominant(text: str, effect: str) -> bool:
    kw, other = EFFECT_KW[effect], EFFECT_KW["promise" if effect == "delivery" else "delivery"]
    return bool(re.search(rf"{DOMINANCE}\W+(?:[\w-]+\W+){{0,6}}?{kw}", text, re.I)
                or re.search(rf"{kw}\W+(?:[\w-]+\W+){{0,6}}?{DOMINANCE}", text, re.I)
                # "slower deliveries, not shorter promises" / "... contributing more than shorter promises"
                or re.search(rf"{kw}\W+(?:[\w-]+\W+){{0,8}}?{CONTRAST}\W+(?:[\w-]+\W+){{0,3}}?{other}", text, re.I))


def score_facts(report: str, key: dict) -> list[dict]:
    """Each fact the report should state, and whether it does."""
    clean = CITE_RE.sub("", report or "")
    answer_sec = _section(clean, "Answer")
    facts = []

    def fact(name, ok, expected):
        facts.append({"fact": name, "ok": bool(ok), "expected": expected})

    abstained = bool(INSUFFICIENT_RE.search(answer_sec))
    if key["expect_insufficient"]:
        fact("says insufficient evidence", abstained, "Insufficient evidence")
        # Data-quality percentages (e.g. "6.2% of orders still open") are the reason to abstain, not a finding.
        late_claims = [x for x in re.split(r"(?<=[.!?])\s+|\n", clean)
                       if re.search(r"\blate|on[- ]time|delay|\brates?\b", x, re.I) and (PCT_NUM_RE.search(x) or PP_NUM_RE.search(x))]
        fact("reports no late rates", not late_claims, "no late rates or changes")
        return facts

    fact("does not wrongly abstain", not abstained, "an answer")
    fact("base late rate", _has_value(PCT_NUM_RE, clean, key["base_rate_pct"]), f"{key['base_rate_pct']:.2f}%")
    fact("current late rate", _has_value(PCT_NUM_RE, clean, key["current_rate_pct"]), f"{key['current_rate_pct']:.2f}%")
    fact("size of the change", _has_value(PP_NUM_RE, clean, key["change_pp"]), f"{key['change_pp']:+.2f} pp")
    fact("direction of the change", (UP_RE if key["direction"] == "up" else DOWN_RE).search(answer_sec), key["direction"])
    says_noise = bool(NOT_SIGNIFICANT_RE.search(clean))
    fact("noise verdict", says_noise == (not key["beyond_noise"]),
         "beyond noise" if key["beyond_noise"] else "within noise")
    if not key["beyond_noise"]:
        return facts  # decomposing noise is meaningless; nothing more to check

    d, p = key["delivery_effect_pp"], key["promise_effect_pp"]
    fact("delivery and promise effects", _has_value(PP_NUM_RE, clean, d) and _has_value(PP_NUM_RE, clean, p),
         f"delivery {d:+.2f} pp, promise {p:+.2f} pp")
    big, small = sorted([abs(d), abs(p)], reverse=True)
    if big >= 1.5 * small:  # only score "which effect is larger" when the answer is clear-cut
        fact("names the larger effect", _names_dominant(clean, key["dominant_effect"]), key["dominant_effect"])
    for dim in ("customer_state", "seller_state"):
        seg = key[f"top_{dim}"]["segment"]
        fact(f"top {dim.replace('_', ' ')}", _names_state(clean, seg), seg)
    return facts


def traceable(report: str, values) -> tuple[int, int]:
    """(numbers in the report, numbers that match some value the system was given)."""
    text = _scrub(CITE_RE.sub(lambda m: " " * len(m.group(0)), report or ""))
    nums = [m.group(0) for m in NUM_RE.finditer(text)]
    vals = {abs(float(v)) for v in values}
    hits = 0
    for raw in nums:
        x = abs(float(raw.replace(",", "").replace("−", "-")))
        hits += _matches(x, len(raw.split(".")[1]) if "." in raw else 0, vals)
    return len(nums), hits


def _evidence_values(rec: RunRecord) -> list[float]:
    out = set()

    def walk(o):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            out.add(float(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            out.add(float(len(o)))
            for v in o:
                walk(v)
    for e in rec.evidence:
        if e["ok"]:
            walk(e["output"])
    return list(out)


# --- Running and scoring ------------------------------------------------------------

def run(systems: list[str], ids: set[str] | None, force: bool):
    con = connect()
    provider = OllamaProvider()
    ok, msg = provider.is_available()
    if not ok:
        raise SystemExit(msg)
    for d in RUNS.values():
        d.mkdir(parents=True, exist_ok=True)
    for q in QUESTIONS:
        if ids and q["id"] not in ids:
            continue
        for system in systems:
            path = RUNS[system] / f"{q['id']}.json"
            if path.exists() and not force:
                continue
            t0 = time.perf_counter()
            if system == "agent":
                rec = investigate(provider, q["base"], q["current"], question_text(q), con=con)
                rec.save(path)
            else:
                path.write_text(json.dumps(run_baseline(provider, con, q), indent=1))
            print(f"{q['id']} {system:8} {time.perf_counter() - t0:6.1f}s", flush=True)


def score() -> dict:
    con = connect()
    rows = []
    for q in QUESTIONS:
        key = answer(con, q["base"], q["current"])
        row = {"id": q["id"], "kind": q["kind"], "base": q["base"], "current": q["current"], "note": q["note"]}
        for system in RUNS:
            path = RUNS[system] / f"{q['id']}.json"
            if not path.exists():
                continue
            if system == "agent":
                rec = RunRecord.load(path)
                n, hits = traceable(rec.report, _evidence_values(rec))
                s = rec.check["summary"]
                row["agent"] = {"report": rec.report, "facts": score_facts(rec.report, key),
                                "numbers": n, "traceable": hits, "cited_correctly": s["supported"],
                                "tool_calls": rec.n_tool_calls, "tool_errors": rec.n_tool_errors,
                                "guard_events": rec.guard_events, "finished": rec.stopped_reason == "report",
                                "latency_s": rec.latency_s, "input_tokens": rec.input_tokens,
                                "output_tokens": rec.output_tokens, "llm_calls": rec.n_llm_calls}
            else:
                b = json.loads(path.read_text())
                n, hits = traceable(b["report"], b["input_values"])
                row["baseline"] = {"report": b["report"], "facts": score_facts(b["report"], key),
                                   "numbers": n, "traceable": hits, "tool_calls": 0, "tool_errors": 0,
                                   "finished": bool(b["report"]), "latency_s": b["latency_s"],
                                   "input_tokens": b["input_tokens"], "output_tokens": b["output_tokens"], "llm_calls": 1}
        row["key"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in key.items()}
        rows.append(row)
    results = {"scored_at": dt.datetime.now().isoformat(timespec="seconds"), "model": OllamaProvider().model,
               "questions": rows, "summary": {s: summarize(rows, s) for s in RUNS if any(s in r for r in rows)}}
    EVAL_DIR.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=1, default=str))
    DOC_PATH.write_text(render_doc(results))
    return results


def summarize(rows: list[dict], system: str) -> dict:
    rs = [r[system] for r in rows if system in r]
    facts = [f for r in rs for f in r["facts"]]
    by_fact: dict[str, list[bool]] = {}
    for f in facts:
        by_fact.setdefault(f["fact"], []).append(f["ok"])
    by_kind: dict[str, list[bool]] = {}
    for r in rows:
        if system in r:
            by_kind.setdefault(r["kind"], []).extend(f["ok"] for f in r[system]["facts"])
    lat = [r["latency_s"] for r in rs]
    numbers = sum(r["numbers"] for r in rs)
    out = {
        "questions": len(rs),
        "fact_accuracy": round(sum(f["ok"] for f in facts) / len(facts), 3),
        "facts_correct": sum(f["ok"] for f in facts), "facts_total": len(facts),
        "fully_correct_questions": sum(all(f["ok"] for f in r["facts"]) for r in rs),
        "by_fact": {k: [sum(v), len(v)] for k, v in by_fact.items()},
        "by_kind": {k: [sum(v), len(v)] for k, v in by_kind.items()},
        "numbers": numbers, "traceable": sum(r["traceable"] for r in rs),
        "traceable_rate": round(sum(r["traceable"] for r in rs) / numbers, 3) if numbers else None,
        "tool_calls": sum(r["tool_calls"] for r in rs), "tool_errors": sum(r["tool_errors"] for r in rs),
        "unfinished_runs": sum(not r["finished"] for r in rs),
        "latency_median_s": round(statistics.median(lat), 1), "latency_max_s": round(max(lat), 1),
        "latency_total_min": round(sum(lat) / 60, 1),
        "input_tokens": sum(r["input_tokens"] for r in rs), "output_tokens": sum(r["output_tokens"] for r in rs),
        "llm_calls": sum(r["llm_calls"] for r in rs), "cost_usd": 0.0,
    }
    if system == "agent":
        out["cited_correctly"] = sum(r["cited_correctly"] for r in rs)
        out["cited_correctly_rate"] = round(out["cited_correctly"] / numbers, 3) if numbers else None
        out["runs_with_guardrail_action"] = sum(bool(r["guard_events"]) for r in rs)
    return out


def _pct(a, b):
    return f"{100 * a / b:.0f}% ({a}/{b})" if b else "n/a"


def render_doc(res: dict) -> str:
    A, B = res["summary"].get("agent"), res["summary"].get("baseline")
    systems = [(n, s) for n, s in (("Agent", A), ("Baseline", B)) if s]
    head = "| | " + " | ".join(n for n, _ in systems) + " |\n|---|" + "---|" * len(systems)
    L = [
        "# Evaluation: agent vs plain AI summary",
        "",
        f"*Generated by `uv run python -m src.evaluate score` on {res['scored_at'][:10]}. Model: `{res['model']}` "
        "running locally through Ollama, for both systems. Do not edit by hand.*",
        "",
        "**Setup.** 25 questions, each asking the real business question (\"Why did the late-delivery rate "
        "*increase* from X to Y?\") for a different pair of months: 9 real rises, 4 falls (the premise is wrong), "
        "4 flat months (the change is noise), 2 year-over-year comparisons, 4 incomplete months and 2 months with "
        "no data. Expected answers come from [`src/answer_key.py`](../src/answer_key.py), a second implementation "
        "written directly against the staging tables; a test checks it agrees with the agent's tools.",
        "",
        "- **Agent:** the Stage 3 agent (four read-only tools, evidence log, guardrails).",
        "- **Baseline:** the same model, no tools, given what the dashboard shows for the two months: the KPI tiles, "
        "the change each tile displays, whether each month is complete, and the small-sample warning. This is what a "
        "basic AI summary of the dashboard sees. It cannot see segments, so it is not expected to name states.",
        "",
        "## Results",
        "",
        head,
        "| Fact accuracy | " + " | ".join(_pct(s["facts_correct"], s["facts_total"]) for _, s in systems) + " |",
        "| Questions fully correct | " + " | ".join(_pct(s["fully_correct_questions"], s["questions"]) for _, s in systems) + " |",
        "| Numbers traceable to data the system saw | " + " | ".join(_pct(s["traceable"], s["numbers"]) for _, s in systems) + " |",
    ]
    if A:
        L.append("| Numbers citing the right evidence item | " + _pct(A["cited_correctly"], A["numbers"]) +
                 (" | n/a (no citations)" if B else "") + " |")
    L += [
        "| Tool calls (failed) | " + " | ".join(f"{s['tool_calls']} ({s['tool_errors']})" for _, s in systems) + " |",
        "| Runs that ended without a report | " + " | ".join(str(s["unfinished_runs"]) for _, s in systems) + " |",
        "| Median / max time per question | " + " | ".join(f"{s['latency_median_s']} s / {s['latency_max_s']} s" for _, s in systems) + " |",
        "| Total time for 25 questions | " + " | ".join(f"{s['latency_total_min']} min" for _, s in systems) + " |",
        "| Model calls | " + " | ".join(str(s["llm_calls"]) for _, s in systems) + " |",
        "| Tokens in / out | " + " | ".join(f"{s['input_tokens']:,} / {s['output_tokens']:,}" for _, s in systems) + " |",
        "| Cost | " + " | ".join("$0 (local model)" for _ in systems) + " |",
        "",
        "### Accuracy by fact",
        "",
        head,
    ]
    names = list(dict.fromkeys(k for _, s in systems for k in s["by_fact"]))
    for k in names:
        L.append(f"| {k} | " + " | ".join(_pct(*s["by_fact"][k]) if k in s["by_fact"] else "n/a" for _, s in systems) + " |")
    L += ["", "### Accuracy by question type", "", head]
    for k in dict.fromkeys(r["kind"] for r in res["questions"]):
        L.append(f"| {k.replace('_', ' ')} | " + " | ".join(_pct(*s["by_kind"][k]) if k in s["by_kind"] else "n/a" for _, s in systems) + " |")
    L += [
        "",
        "## How each fact is checked",
        "",
        "| Fact | Applies to | Counted as correct when the report… |",
        "|---|---|---|",
        "| says insufficient evidence | incomplete / missing months | has \"insufficient evidence\" in its Answer section |",
        "| reports no late rates | incomplete / missing months | has no sentence about lateness that contains a percentage or pp (data-quality percentages given as the reason are allowed) |",
        "| does not wrongly abstain | complete months | does not say \"insufficient evidence\" in its Answer |",
        "| base / current late rate | complete months | contains the rate as a percentage, at the precision it shows (14.1% matches 14.13%) |",
        "| size of the change | complete months | contains the change in percentage points |",
        "| direction of the change | complete months | uses a rise word (rose, increased…) or a fall word (fell, decreased…) in its Answer |",
        "| noise verdict | complete months | says the change is not significant / within noise exactly when the answer key does (\\|z\\| < 2) |",
        "| delivery and promise effects | changes beyond noise | contains both the slower-delivery and shorter-promise effects in pp |",
        "| names the larger effect | when one effect is at least 1.5× the other | says which effect was the main one: \"mostly\", \"largest\"… near \"promise\" or \"slower\", or \"slower deliveries, not shorter promises\" |",
        "| top customer / seller state | changes beyond noise | names the state with the largest contribution to the change (code or name) |",
        "",
        "Numbers are *traceable* when they match, at the precision shown, some number the system was given: the "
        "tool results for the agent, the dashboard tiles for the baseline. A number that matches nothing was "
        "made up or calculated by the model. Dates, years and list numbering are ignored.",
        "",
        "## Per question",
        "",
        "| # | Months | Type | Expected | Agent | Baseline |",
        "|---|---|---|---|---|---|",
    ]
    for r in res["questions"]:
        k = r["key"]
        exp = ("insufficient evidence" if k["expect_insufficient"] else
               f"{k['base_rate_pct']}% → {k['current_rate_pct']}% ({k['change_pp']:+} pp" +
               (", noise)" if not k["beyond_noise"] else
                f"; delivery {k['delivery_effect_pp']:+}, promise {k['promise_effect_pp']:+}; top {k['top_customer_state']['segment']} / {k['top_seller_state']['segment']})"))

        def cell(s):
            if s not in r:
                return "not run"
            f = r[s]["facts"]
            missed = [x["fact"] for x in f if not x["ok"]]
            return f"{sum(x['ok'] for x in f)}/{len(f)}" + (f" — missed: {', '.join(missed)}" if missed else "")
        L.append(f"| {r['id']} | {r['base']} → {r['current']} | {r['kind'].replace('_', ' ')} | {exp} | {cell('agent')} | {cell('baseline')} |")
    L += ["", "Full reports, evidence and every check: `eval/results.json`, `eval/runs/`, and the Evaluation page of the app.", ""]
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description="Run or score the evaluation.")
    p.add_argument("command", choices=["run", "score"])
    p.add_argument("--systems", default="agent,baseline")
    p.add_argument("--ids", default="", help="comma-separated question ids, e.g. q01,q10")
    p.add_argument("--force", action="store_true", help="re-run questions that already have a saved run")
    a = p.parse_args()
    if a.command == "run":
        run(a.systems.split(","), set(filter(None, a.ids.split(","))) or None, a.force)
    res = score()
    for name, s in res["summary"].items():
        print(f"{name:8} accuracy {s['fact_accuracy']:.0%} ({s['facts_correct']}/{s['facts_total']}), "
              f"fully correct {s['fully_correct_questions']}/{s['questions']}, traceable {s['traceable_rate']}, "
              f"tool errors {s['tool_errors']}/{s['tool_calls']}, median {s['latency_median_s']}s")
    print(f"wrote {DOC_PATH.relative_to(ROOT)} and {RESULTS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
