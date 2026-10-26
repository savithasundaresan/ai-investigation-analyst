"""Investigate: run the agent live on the local model, or replay a saved run.

Every number in the report links to an evidence item, and each evidence item
shows the tool call, what it returned, and the exact SQL that ran.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from src.agent import RunRecord, investigate, replay
from src.config import ROOT
from src.db import connect
from src.llm import OllamaProvider

st.set_page_config(page_title="Investigate", layout="wide")


@st.cache_resource
def get_con():
    return connect()


con = get_con()
months = [f"{r[0]:%Y-%m}" for r in con.execute("SELECT purchase_month FROM dim_month ORDER BY 1").fetchall()]
complete = {f"{r[0]:%Y-%m}" for r in con.execute("SELECT purchase_month FROM dim_month WHERE is_complete").fetchall()}

st.title("Investigate a late-delivery change")
st.info("**Simulation: historical replay.** The agent investigates a past month as if it were current. "
        "The model only chooses which analysis to run next and writes the explanation. Every number comes from SQL "
        "and is checked against the evidence it cites.", icon="🕰️")

mode = st.radio("Mode", ["Replay a saved investigation", "Run live on the local model"], horizontal=True, key="mode")

TOOL_LABEL = {"check_data_quality": "Checked data quality", "compare_periods": "Compared periods",
              "break_down_metric": "Broke down the change", "inspect_orders": "Inspected orders"}


def describe(ev: dict) -> str:
    if ev["type"] == "guard":
        return "Guardrail: sent the draft back — " + ev["message"].splitlines()[0]
    args = {k: v for k, v in ev["args"].items() if k not in ("base_month", "current_month")}
    head = f"{ev['evidence_id']} · " if ev.get("evidence_id") else "✗ "
    return head + TOOL_LABEL.get(ev["tool"], ev["tool"]) + (f" ({json.dumps(args)})" if args else "") + \
        (f" — error: {ev['error']}" if ev.get("error") else "")


rec: RunRecord | None = None
if mode.startswith("Replay"):
    files = sorted((ROOT / "replays").glob("*.json"))
    if not files:
        st.warning("No saved runs in replays/. Run one live first.")
        st.stop()
    labels = {}
    for f in files:
        saved = json.loads(f.read_text())
        labels[f] = f"{saved['current_month']} vs {saved['base_month']} · {saved['model']} · recorded {saved['started_at'][:10]}"
    default = next((i for i, f in enumerate(files) if f.name.startswith("2018-03_vs_2018-02")), 0)
    choice = st.selectbox("Saved investigation", files, index=default, format_func=labels.get, key="replay_file")
    st.caption("Replay feeds the saved model responses through the same agent loop. Every tool call is executed "
               "again against the database, so the evidence is recomputed, not copied.")
    if st.button("Replay", type="primary", key="replay_btn"):
        with st.status("Replaying…", expanded=True) as status:
            rec = replay(choice, con, on_event=lambda e: st.write(describe(e)))
            # Show the original run's model time and tokens, not the replay's (which uses no model).
            saved = RunRecord.load(choice)
            rec.latency_s, rec.input_tokens, rec.output_tokens = saved.latency_s, saved.input_tokens, saved.output_tokens
            status.update(label="Replay finished", state="complete")
else:
    c1, c2 = st.columns(2)
    base = c1.selectbox("Base month", months, index=months.index("2018-02"), key="live_base")
    current = c2.selectbox("Current month", months, index=months.index("2018-03"), key="live_current")
    if base not in complete or current not in complete:
        st.caption("One of these months is incomplete; a correct answer is “insufficient evidence”.")
    provider = OllamaProvider()
    ok, msg = provider.is_available()
    if not ok:
        st.error(msg)
        st.stop()
    st.caption(f"Model: {provider.model} on this computer via Ollama. Free; a run takes about one to two minutes.")
    if st.button("Investigate", type="primary", key="live_btn"):
        with st.status(f"Investigating {current} vs {base}…", expanded=True) as status:
            rec = investigate(provider, base, current, con=con, on_event=lambda e: st.write(describe(e)))
            rec.save()
            status.update(label="Investigation finished", state="complete")

if rec is not None:
    st.session_state["last_run"] = rec
rec = st.session_state.get("last_run")
if rec is None:
    st.stop()

# --- Result -----------------------------------------------------------------------

s = rec.check["summary"]
m = st.columns(6)
m[0].metric("Evidence supported", f"{s['supported']}/{s['numbers']}", help="Numbers in the report that match the evidence they cite.")
m[1].metric("Tool calls", rec.n_tool_calls)
m[2].metric("Tool errors", rec.n_tool_errors)
m[3].metric("Guardrail actions", len(rec.guard_events))
m[4].metric("Time", f"{rec.latency_s:.0f} s", help="For a replay, the time the original live run took.")
m[5].metric("Cost", "$0.00" if rec.provider.endswith("ollama") or rec.provider.startswith("replay") else "see tokens",
            help=f"{rec.input_tokens:,} input / {rec.output_tokens:,} output tokens")

left, right = st.columns([3, 2])
with left:
    st.subheader("Report")
    problems = {n["text"] for n in rec.check["numbers"] if n["status"] != "supported"}
    body = re.sub(r"\[(E\d+(?:\s*,\s*E\d+)*)\]", lambda mm: f" :blue-badge[{mm.group(1)}]", rec.report)
    for p in problems:  # make unsupported numbers stand out
        body = re.sub(rf"(?<![\w.]){re.escape(p)}(?![\w])", f":red-background[{p}]", body)
    st.markdown(body)
    if problems:
        st.caption("Numbers highlighted in red are not backed by the evidence they cite.")
with right:
    st.subheader("Evidence")
    for ev in rec.evidence:
        title = f"{ev['id'] or '✗'} · {ev['tool']}" + ("" if ev["ok"] else " (failed)")
        with st.expander(title):
            st.markdown("**Arguments**")
            st.json(ev["args"], expanded=False)
            st.markdown("**Returned to the model**")
            st.json(ev["output"], expanded=False)
            for q in ev["queries"]:
                st.code(q, language="sql")

with st.expander("Timeline and guardrails"):
    for t in rec.transcript:
        st.write(describe(t))
    for g in rec.guard_events:
        st.write("• " + g)
with st.expander("Citation check, number by number"):
    if rec.check["numbers"]:
        st.dataframe(pd.DataFrame(rec.check["numbers"])[["text", "status", "cited", "sentence"]], hide_index=True, width="stretch")
    else:
        st.write("The report contains no numbers to check.")
