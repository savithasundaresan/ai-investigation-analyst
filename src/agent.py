"""The investigation agent: one model, four read-only tools, an evidence log.

    uv run python -m src.agent --base 2018-02 --current 2018-03
    uv run python -m src.agent --replay replays/feb_mar_2018.json

The model decides which tool to call next from what earlier tools returned.
The code around it enforces three guardrails:
  1. Coverage: a report written before the required steps (data quality for
     both months; if both are complete, compare_periods on late_rate and
     break-downs by at least two dimensions) is sent back once with the gaps.
  2. Citations: every number must match the evidence it cites (src/report.py);
     a report that fails gets one repair round with the exact problems listed.
  3. Limits: at most MAX_LLM_CALLS model calls; repeated tool errors stop the run.
Every run is saved as JSON (model responses + evidence), so it can be replayed
without a model and scored by the evaluation.
"""

import argparse
import datetime as dt
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from src.config import ROOT
from src.db import connect
from src.llm import LLMProvider, ReplayProvider, dumps, get_provider
from src.report import check_report
from src.tools import TOOL_SCHEMAS, ToolBox

MAX_LLM_CALLS = 12
MAX_CONSECUTIVE_TOOL_ERRORS = 3
RUNS_DIR = ROOT / "data" / "runs"

SYSTEM_PROMPT = """You are a data analyst investigating delivery performance at Olist, a Brazilian e-commerce marketplace. \
The data is historical (2016-2018). The user is replaying a past month as if it were the current month.

You learn numbers only by calling tools. Every successful tool result has an evidence_id such as "E3".

How to investigate:
1. Call check_data_quality for both months first. If a month is not complete, or any tool returns \
"insufficient_evidence": true, stop investigating: start the Answer with "Insufficient evidence", explain why with \
citations, and do not report rates, changes or causes.
2. Call compare_periods with metric late_rate to confirm the change and whether it is beyond noise. Read \
delivery_vs_promise: did deliveries get slower, or did the promised delivery windows get shorter?
3. Call break_down_metric for late_rate with at least two different dimensions (for example seller_state and \
customer_state, or category). Decide whether the change is mix (orders shifted toward segments that were already \
worse) or performance (segments themselves got worse). Note any flagged segments.
4. Optionally call inspect_orders for the most affected segment.
5. Then write the report.

Report rules:
- Copy every number from a tool result and put its citation right after it, for example: \
"the late rate rose from 14.1% to 19.0% [E2]". Never calculate, add, subtract or round numbers yourself.
- Describe associations, not causes: the data shows what changed together, not why.
- If the evidence is insufficient, say so plainly.
- Do not estimate revenue, savings or costs, and do not claim that any action was taken.

Write the report in this format:
## Answer
Two or three sentences.
## Findings
Bullet points, each with numbers and citations. Always include: the delivery_effect_pp and promise_effect_pp from \
compare_periods, and the mix_pp and performance_pp from each break-down.
## Investigate first
One to three concrete things to look into, most important first.
## Limitations
Bullet points."""

def coverage_gaps(evidence, base_month: str, current_month: str) -> list[str]:
    """Steps the playbook requires that the run has not done yet."""
    ok = [e for e in evidence if e.ok]
    dq_months = {m["month"]: m["complete"] for e in ok if e.tool == "check_data_quality" for m in e.output["months"]}
    if base_month not in dq_months or current_month not in dq_months:
        return [f"call check_data_quality with months [{base_month!r}, {current_month!r}]"]
    if not (dq_months[base_month] and dq_months[current_month]):
        return []  # incomplete month: the right answer is "insufficient evidence", not more analysis
    gaps = []
    if not any(e.tool == "compare_periods" and e.args.get("metric") == "late_rate" for e in ok):
        gaps.append(f"call compare_periods with metric 'late_rate', base_month {base_month!r}, current_month {current_month!r}")
    dims = {e.args.get("dimension") for e in ok if e.tool == "break_down_metric" and e.args.get("metric") == "late_rate"}
    if len(dims) < 2:
        gaps.append(f"call break_down_metric for late_rate by {2 - len(dims)} more dimension(s); done so far: {sorted(dims) or 'none'}")
    return gaps


def coverage_message(gaps: list[str]) -> str:
    return ("Before writing the report, complete these steps:\n" + "\n".join(f"- {g}" for g in gaps) +
            "\nThen write the full report again using all the evidence.")


def repair_message(problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems[:15])
    return ("Your report has numbers that are not backed by the evidence they cite:\n" + listed +
            "\nRewrite the full report. Use only numbers that appear in tool results, each followed by the citation of "
            "the tool result that contains it. Remove any number you cannot cite. Do not call tools.")


@dataclass
class RunRecord:
    question: str
    base_month: str
    current_month: str
    provider: str
    model: str
    started_at: str
    report: str = ""
    responses: list[dict] = field(default_factory=list)   # every model response, in order (for replay)
    transcript: list[dict] = field(default_factory=list)  # human-readable timeline
    evidence: list[dict] = field(default_factory=list)
    check: dict = field(default_factory=dict)
    guard_events: list[str] = field(default_factory=list)
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    n_tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    stopped_reason: str = ""

    def save(self, path: Path | None = None) -> Path:
        if path is None:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", f"{self.base_month}-{self.current_month}-{self.provider}".lower())
            path = RUNS_DIR / f"{self.started_at.replace(':', '').replace('-', '')[:15]}_{slug}.json"
        path.write_text(json.dumps(asdict(self), indent=1, default=str))
        return path

    @classmethod
    def load(cls, path: Path) -> "RunRecord":
        return cls(**json.loads(Path(path).read_text()))


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def investigate(provider: LLMProvider, base_month: str, current_month: str, question: str | None = None,
                con=None, on_event: Callable[[dict], None] | None = None) -> RunRecord:
    con = con or connect()
    tools = ToolBox(con)
    question = question or (f"Why did the late-delivery rate change from {base_month} to {current_month}? "
                            "Which sellers, regions or product categories contributed most, and what should we "
                            "investigate first?")
    rec = RunRecord(question=question, base_month=base_month, current_month=current_month,
                    provider=provider.name, model=provider.model,
                    started_at=dt.datetime.now().isoformat(timespec="seconds"))
    messages: list[dict] = [{"role": "user", "content": f"{question}\nBase month: {base_month}. Current month: {current_month}."}]
    t0 = time.perf_counter()
    consecutive_errors = 0
    nudged = repaired = False

    def log(event: dict):
        rec.transcript.append(event)
        if on_event:
            on_event(event)

    def llm(tool_schemas):
        resp = provider.chat(SYSTEM_PROMPT, messages, tool_schemas)
        rec.responses.append(resp.to_dict())
        rec.n_llm_calls += 1
        rec.input_tokens += resp.input_tokens
        rec.output_tokens += resp.output_tokens
        return resp

    while True:
        if rec.n_llm_calls >= MAX_LLM_CALLS:
            rec.guard_events.append(f"stopped after {MAX_LLM_CALLS} model calls")
            rec.stopped_reason = "max_calls"
            break
        resp = llm(TOOL_SCHEMAS)
        text = _clean(resp.text)
        messages.append({"role": "assistant", "content": text, "tool_calls": resp.tool_calls, "raw": resp.raw})

        if resp.tool_calls:
            for call in resp.tool_calls:
                ev = tools.call(call.name, call.arguments)
                rec.n_tool_calls += 1
                rec.n_tool_errors += not ev.ok
                consecutive_errors = 0 if ev.ok else consecutive_errors + 1
                log({"type": "tool", "tool": call.name, "args": call.arguments, "evidence_id": ev.id,
                                       "ok": ev.ok, "error": ev.output.get("error"), "note": text[:400] or None})
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": dumps(ev.output), "is_error": not ev.ok})
            if consecutive_errors >= MAX_CONSECUTIVE_TOOL_ERRORS:
                rec.guard_events.append(f"stopped after {consecutive_errors} tool errors in a row")
                rec.stopped_reason = "tool_errors"
                break
            continue

        # The model wrote a report.
        gaps = coverage_gaps(tools.evidence, base_month, current_month)
        if gaps and not nudged:
            nudged = True
            msg = coverage_message(gaps)
            rec.guard_events.append(f"report written before required steps; sent back ({len(gaps)} gaps)")
            log({"type": "guard", "message": msg, "draft": text})
            messages.append({"role": "user", "content": msg})
            continue

        ev_map = {e.id: e.output for e in tools.evidence if e.ok}
        check = check_report(text, ev_map)
        if check.problems() and not repaired and ev_map:
            repaired = True
            first = (text, check)
            msg = repair_message(check.problems() or ["the report has no citations"])
            rec.guard_events.append(f"citation check failed ({check.summary()}); one repair round")
            log({"type": "guard", "message": msg, "draft": text})
            messages.append({"role": "user", "content": msg})
            resp2 = llm([])
            text2 = _clean(resp2.text)
            messages.append({"role": "assistant", "content": text2, "tool_calls": [], "raw": resp2.raw})
            check2 = check_report(text2, ev_map)
            # Keep whichever draft is better supported.
            if text2 and (check2.support_rate, -len(check2.problems())) >= (first[1].support_rate, -len(first[1].problems())):
                text, check = text2, check2
            else:
                rec.guard_events.append("repair did not improve the report; kept the first draft")
        rec.report = text
        rec.check = check.to_dict()
        rec.stopped_reason = "report"
        break

    if not rec.report:
        rec.report = ("## Answer\nThe investigation stopped before a report was written "
                      f"({rec.stopped_reason}). The evidence gathered so far is listed below.")
        rec.check = check_report(rec.report, {}).to_dict()
    rec.evidence = [asdict(e) for e in tools.evidence]
    rec.latency_s = round(time.perf_counter() - t0, 2)
    return rec


def replay(path: Path, con=None, on_event: Callable[[dict], None] | None = None) -> RunRecord:
    """Re-run a saved investigation without a model: the saved responses drive
    the same loop, and every tool call is executed again against the database."""
    saved = RunRecord.load(path)
    rec = investigate(ReplayProvider(saved.responses, model=saved.model), saved.base_month, saved.current_month,
                      saved.question, con, on_event)
    rec.provider, rec.model = f"replay of {saved.provider}", saved.model
    return rec


def main():
    p = argparse.ArgumentParser(description="Run or replay a late-delivery investigation.")
    p.add_argument("--base", default="2018-02")
    p.add_argument("--current", default="2018-03")
    p.add_argument("--provider", default=None, help="ollama (default) or anthropic")
    p.add_argument("--replay", type=Path, help="replay a saved run instead of calling a model")
    p.add_argument("--save-as", type=Path, help="also save the run to this path (e.g. replays/x.json)")
    a = p.parse_args()
    rec = replay(a.replay) if a.replay else investigate(get_provider(a.provider), a.base, a.current)
    path = rec.save()
    if a.save_as:
        rec.save(a.save_as)
    print(rec.report)
    print("\n---")
    for t in rec.transcript:
        print(f"  {t.get('evidence_id') or '--'}  {t['type']}: {t.get('tool', '')} {json.dumps(t.get('args', ''))[:120]} {t.get('error') or ''}")
    print(f"\ncitation check: {rec.check['summary']}")
    print(f"guards: {rec.guard_events}")
    print(f"{rec.n_llm_calls} model calls, {rec.n_tool_calls} tool calls ({rec.n_tool_errors} failed), "
          f"{rec.input_tokens:,} in / {rec.output_tokens:,} out tokens, {rec.latency_s:.1f}s")
    print(f"saved: {path}")


if __name__ == "__main__":
    main()
