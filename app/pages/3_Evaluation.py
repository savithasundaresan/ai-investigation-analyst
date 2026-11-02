"""Evaluation: the agent vs a plain AI summary on 25 questions with known answers.

Reads eval/results.json, written by `uv run python -m src.evaluate score`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.evaluate import RESULTS_PATH

BLUE, GREY = "#2a78d6", "#a3a29d"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"

st.set_page_config(page_title="Evaluation", layout="wide")
st.title("Evaluation: agent vs plain AI summary")

if not RESULTS_PATH.exists():
    st.warning("No results yet. Run `uv run python -m src.evaluate run` (about an hour), or `score` if runs are saved.")
    st.stop()

res = json.loads(RESULTS_PATH.read_text())
A, B = res["summary"]["agent"], res["summary"]["baseline"]
st.caption(f"25 questions, each asking why the late-delivery rate *increased* between two months: real rises, "
           f"falls (wrong premise), flat months (noise), incomplete and missing months. Both systems use "
           f"`{res['model']}` locally. Expected answers come from an independent SQL answer key. Scored {res['scored_at'][:10]}.")

c = st.columns(5)
c[0].metric("Agent accuracy", f"{A['fact_accuracy']:.0%}", f"{100 * (A['fact_accuracy'] - B['fact_accuracy']):+.0f} pts",
            help="Share of answer-key facts the agent's reports state correctly; the change is vs the baseline.")
c[1].metric("Baseline accuracy", f"{B['fact_accuracy']:.0%}", help="Same model, given only the dashboard's tiles.")
c[2].metric("Traceable numbers", f"{A['traceable_rate']:.0%}",
            help=f"Agent numbers that match a tool result. Baseline: {B['traceable_rate']:.0%} match the dashboard tiles.")
c[3].metric("Tool failures", f"{A['tool_errors']}/{A['tool_calls']}",
            help="Calls the tools rejected, e.g. asking about a month with no data. The error goes back to the model.")
c[4].metric("Median time", f"{A['latency_median_s']:.0f} s", f"baseline {B['latency_median_s']:.0f} s", delta_color="off",
            delta_arrow="off", help="Per question, on this computer. Cost: $0, local model.")

# Accuracy by fact
facts = list(dict.fromkeys(list(A["by_fact"]) + list(B["by_fact"])))
fig = go.Figure()
for name, s, color in (("Agent", A, BLUE), ("Baseline", B, GREY)):
    vals = [100 * s["by_fact"][f][0] / s["by_fact"][f][1] if f in s["by_fact"] else None for f in facts]
    labels = [f"{s['by_fact'][f][0]}/{s['by_fact'][f][1]}" if f in s["by_fact"] else "" for f in facts]
    fig.add_bar(y=facts, x=vals, name=name, orientation="h", marker_color=color, text=labels,
                textposition="outside", textfont=dict(color=MUTED, size=11),
                hovertemplate="%{y}: %{x:.0f}%<extra>" + name + "</extra>")
fig.update_layout(barmode="group", height=60 + 42 * len(facts), margin=dict(l=10, r=40, t=30, b=10),
                  plot_bgcolor="white", paper_bgcolor="white", font=dict(color=INK),
                  legend=dict(orientation="h", y=1.06, x=0), bargap=0.25, bargroupgap=0.08,
                  xaxis=dict(range=[0, 112], ticksuffix="%", gridcolor=GRID, zeroline=False),
                  yaxis=dict(autorange="reversed"))
st.subheader("Accuracy by fact")
st.plotly_chart(fig, width="stretch")

with st.expander("All metrics side by side"):
    rows = [("Fact accuracy", f"{A['facts_correct']}/{A['facts_total']}", f"{B['facts_correct']}/{B['facts_total']}"),
            ("Questions fully correct", f"{A['fully_correct_questions']}/{A['questions']}", f"{B['fully_correct_questions']}/{B['questions']}"),
            ("Numbers traceable to data seen", f"{A['traceable']}/{A['numbers']}", f"{B['traceable']}/{B['numbers']}"),
            ("Numbers citing the right evidence", f"{A['cited_correctly']}/{A['numbers']}", "n/a"),
            ("Tool calls (failed)", f"{A['tool_calls']} ({A['tool_errors']})", "0"),
            ("Runs without a report", A["unfinished_runs"], B["unfinished_runs"]),
            ("Runs where a guardrail acted", A["runs_with_guardrail_action"], "n/a"),
            ("Median / max time", f"{A['latency_median_s']} s / {A['latency_max_s']} s", f"{B['latency_median_s']} s / {B['latency_max_s']} s"),
            ("Tokens in / out", f"{A['input_tokens']:,} / {A['output_tokens']:,}", f"{B['input_tokens']:,} / {B['output_tokens']:,}"),
            ("Cost", "$0", "$0")]
    st.dataframe(pd.DataFrame(rows, columns=["Metric", "Agent", "Baseline"]).astype(str), hide_index=True, width="stretch")

# Per question
st.subheader("Per question")
table = []
for r in res["questions"]:
    k = r["key"]
    exp = "insufficient evidence" if k["expect_insufficient"] else f"{k['change_pp']:+} pp" + ("" if k["beyond_noise"] else " (noise)")
    table.append({"#": r["id"], "months": f"{r['base']} → {r['current']}", "type": r["kind"].replace("_", " "), "expected": exp,
                  "agent": "{}/{}".format(sum(f["ok"] for f in r["agent"]["facts"]), len(r["agent"]["facts"])) if "agent" in r else "–",
                  "baseline": "{}/{}".format(sum(f["ok"] for f in r["baseline"]["facts"]), len(r["baseline"]["facts"])) if "baseline" in r else "–"})
st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

by_id = {r["id"]: r for r in res["questions"]}
pick = st.selectbox("Compare the two answers for one question", list(by_id),
                    format_func=lambda i: f"{i} · {by_id[i]['base']} → {by_id[i]['current']} · {by_id[i]['kind'].replace('_', ' ')}")
r = by_id[pick]
cols = st.columns(2)
for col, system in zip(cols, ("agent", "baseline")):
    with col:
        st.markdown(f"**{system.capitalize()}**")
        if system not in r:
            st.write("Not run.")
            continue
        s = r[system]
        st.dataframe(pd.DataFrame([{"ok": "✓" if f["ok"] else "✗", "fact": f["fact"], "expected": str(f["expected"])}
                                   for f in s["facts"]]), hide_index=True, width="stretch")
        st.caption(f"{s['traceable']}/{s['numbers']} numbers traceable · {s['latency_s']:.0f} s · "
                   f"{s['tool_calls']} tool calls ({s['tool_errors']} failed)")
        with st.container(border=True):
            st.markdown(s["report"].replace("## ", "##### "))
