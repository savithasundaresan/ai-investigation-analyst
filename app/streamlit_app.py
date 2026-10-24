"""Late-delivery dashboard.

    uv run streamlit run app/streamlit_app.py

Every number on this page comes from src/metrics.py, the same functions the
agent's tools call. Each section has a "Show SQL" expander with the exact query.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import metrics as M
from src.config import MIN_ABS_Z, MIN_DELTA_PP, MIN_N
from src.db import connect

# Chart colours: validated categorical slots 1-3 plus neutral ink.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"

st.set_page_config(page_title="Late-Delivery Investigator", layout="wide")


@st.cache_resource
def get_con():
    return connect()


con = get_con()


# Thin cached wrappers (Streamlit needs hashable arguments).
@st.cache_data
def months():
    return M.complete_months(con)


@st.cache_data
def kpis(month_list, filters):
    return M.kpis(con, list(month_list) if month_list else None, dict(filters))


@st.cache_data
def decompose(metric, dimension, base, current, filters):
    return M.decompose_mix(con, metric, dimension, base, current, dict(filters))


@st.cache_data
def breakdown(metric, dimension, base, current, filters):
    return M.break_down(con, metric, dimension, base, current, dict(filters))


@st.cache_data
def promise(base, current, filters):
    return M.promise_vs_speed(con, base, current, dict(filters))


@st.cache_data
def dq(month_list):
    return M.data_quality(con, list(month_list))


@st.cache_data
def options(column, table):
    return [r[0] for r in con.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY 1").fetchall()]


def show_sql(result, label="Show SQL"):
    with st.expander(label):
        st.code(result.sql_with_params(), language="sql")


def style(fig, height=320, y_title=None, y_fmt=None):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=28, b=8), plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, size=13), hovermode="x unified", showlegend=False,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED), title=y_title, tickformat=y_fmt)
    return fig


def fmt_month(d):
    return d.strftime("%b %Y")


# --- Sidebar: period and filters ---------------------------------------------

all_months = months()
with st.sidebar:
    st.header("Period")
    current = st.selectbox("Current month", all_months[1:], index=all_months[1:].index(pd.Timestamp("2018-03-01").date()),
                           format_func=fmt_month, key="current")
    base_choices = [m for m in all_months if m < current]
    base = st.selectbox("Compare with", base_choices, index=len(base_choices) - 1, format_func=fmt_month, key="base")
    st.header("Filters")
    f_cust = st.selectbox("Customer state", ["All"] + options("customer_state", "fct_orders"), key="f_cust")
    f_sell = st.selectbox("Seller state", ["All"] + options("seller_state", "bridge_order_segment"), key="f_sell")
    f_cat = st.selectbox("Product category", ["All"] + options("category", "bridge_order_segment"), key="f_cat")
    st.caption(f"Anomaly rules: n ≥ {MIN_N} in both months, |z| ≥ {MIN_ABS_Z:g}, change ≥ {MIN_DELTA_PP:g} pp.")

filters = tuple((k, v) for k, v in [("customer_state", f_cust), ("seller_state", f_sell), ("category", f_cat)] if v != "All")

# --- Header -------------------------------------------------------------------

st.title("Late-Delivery Investigator")
st.info(f"**Simulation: historical replay.** Olist marketplace data, {fmt_month(all_months[0])} to "
        f"{fmt_month(all_months[-1])}. *Current* is {fmt_month(current)}, compared with {fmt_month(base)}.", icon="🕰️")
if filters:
    st.caption("Filtered to " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in filters))

# --- KPI tiles ----------------------------------------------------------------

k = kpis((base, current), filters)
kd = k.data.set_index(k.data["purchase_month"].dt.date)
if current not in kd.index or base not in kd.index:
    st.warning("No orders match these filters in one of the two months.")
    st.stop()
b, c = kd.loc[base], kd.loc[current]
small = c["delivered"] < MIN_N or b["delivered"] < MIN_N
if c["delivered"] == 0 or b["delivered"] == 0:
    st.warning("No delivered orders in one of the two months for these filters, so there is no late rate to explain.")
    st.stop()
if small:
    st.warning(f"Fewer than {MIN_N} delivered orders in one month: rates below are too noisy to act on.")


def tile(col, label, cur, prev, fmt, pp=False, bad_up=True, help=None):
    delta = (cur - prev) * (100 if pp else 1)
    delta_txt = f"{delta:+.1f} pp" if pp else (f"{delta:+,.1f}" if isinstance(delta, float) else f"{delta:+,}")
    col.metric(label, fmt(cur), delta_txt, delta_color="inverse" if bad_up else "normal", help=help)


r1 = st.columns(4)
tile(r1[0], "Late rate", c.late_rate, b.late_rate, lambda v: f"{v:.1%}", pp=True,
     help=f"{int(c.late_orders):,} of {int(c.delivered):,} delivered orders arrived after the estimated date.")
tile(r1[1], "Overdue, undelivered", int(c.overdue_undelivered), int(b.overdue_undelivered), lambda v: f"{v:,}",
     help="Not delivered, not canceled, and the estimate has passed. Not counted in the late rate.")
tile(r1[2], "Delivery days", c.avg_delivery_days, b.avg_delivery_days, lambda v: f"{v:.1f}",
     help="Average days from purchase to delivery, delivered orders.")
tile(r1[3], "Promised days", c.avg_promised_days, b.avg_promised_days, lambda v: f"{v:.1f}", bad_up=False,
     help="Average days from purchase to the estimated delivery date. Shorter promises make lateness more likely.")
r2 = st.columns(4)
tile(r2[0], "Orders", int(c.orders), int(b.orders), lambda v: f"{v:,}", bad_up=False)
tile(r2[1], "Order value (R$)", c.aov, b.aov, lambda v: f"{v:,.2f}", bad_up=False)
tile(r2[2], "Low reviews (1–2★)", c.low_review_rate, b.low_review_rate, lambda v: f"{v:.1%}", pp=True)
tile(r2[3], "Canceled", c.cancellation_rate, b.cancellation_rate, lambda v: f"{v:.1%}", pp=True)
show_sql(k, "Show SQL for these tiles")

# --- Trends -------------------------------------------------------------------

st.subheader("Trend")
t = kpis(None, filters)
td = t.data.assign(month=t.data["purchase_month"].dt.date)
tc1, tc2 = st.columns(2)
with tc1:
    st.markdown("**Late-delivery rate by purchase month**")
    fig = go.Figure(go.Scatter(x=td.month, y=td.late_rate, mode="lines", line=dict(color=BLUE, width=2),
                               name="Late rate", hovertemplate="%{y:.1%}<extra></extra>"))
    hl = td[td.month.isin([base, current])]
    fig.add_trace(go.Scatter(x=hl.month, y=hl.late_rate, mode="markers+text", marker=dict(color=BLUE, size=10, line=dict(color="white", width=2)),
                             text=[f"{v:.1%}" for v in hl.late_rate],
                             textposition=["middle left" if m == base else "top center" for m in hl.month], textfont=dict(color=INK),
                             hoverinfo="skip"))
    st.plotly_chart(style(fig, y_fmt=".0%"), width="stretch")
with tc2:
    st.markdown("**Promised vs actual delivery time (days)**")
    fig = go.Figure()
    for col, name, color in [("avg_promised_days", "Promised", ORANGE), ("avg_delivery_days", "Actual", BLUE)]:
        fig.add_trace(go.Scatter(x=td.month, y=td[col], mode="lines", line=dict(color=color, width=2), name=name,
                                 hovertemplate=f"{name}: %{{y:.1f}} days<extra></extra>"))
        fig.add_annotation(x=td.month.iloc[-1], y=td[col].iloc[-1], text=name, showarrow=False, xanchor="left", xshift=6,
                           font=dict(color=INK, size=12))
    fig.add_vrect(x0=base, x1=current, fillcolor=GRID, opacity=0.5, line_width=0)
    fig = style(fig, y_title=None)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.12, x=0), margin=dict(r=60))
    st.plotly_chart(fig, width="stretch")
st.caption("An order is late when actual days exceed promised days. The gap between the lines is the slack; "
           "when it narrows, lateness rises even if deliveries don't slow down.")
show_sql(t, "Show SQL for the trend")

# --- Why did it change? ----------------------------------------------------------

st.subheader(f"Why did the late rate change from {fmt_month(base)} to {fmt_month(current)}?")


def waterfall(start_label, start, steps, end_label, end, colors):
    fig = go.Figure()
    x = [start_label] + [s[0] for s in steps] + [end_label]
    running = start
    fig.add_trace(go.Bar(x=[start_label], y=[start], marker_color=MUTED, text=[f"{start:.1%}"], textposition="outside",
                         hovertemplate="%{y:.2%}<extra></extra>"))
    for (label, val), color in zip(steps, colors):
        fig.add_trace(go.Bar(x=[label], y=[abs(val)], base=[running if val >= 0 else running + val], marker_color=color,
                             text=[f"{100 * val:+.1f} pp"], textposition="outside", hovertemplate=f"{100 * val:+.2f} pp<extra></extra>"))
        running += val
    fig.add_trace(go.Bar(x=[end_label], y=[end], marker_color=MUTED, text=[f"{end:.1%}"], textposition="outside",
                         hovertemplate="%{y:.2%}<extra></extra>"))
    fig.update_traces(marker_line_width=0, cliponaxis=False, textfont=dict(color=INK))
    fig = style(fig, height=300, y_fmt=".0%")
    fig.update_layout(bargap=0.45, hovermode="closest")
    fig.update_xaxes(categoryorder="array", categoryarray=x)
    return fig


w1, w2 = st.columns(2)
with w1:
    st.markdown("**1 · Slower deliveries or shorter promises?**")
    p = promise(base, current, filters)
    n = p.notes
    st.plotly_chart(waterfall(fmt_month(base), n["base_rate"],
                              [("Slower deliveries", n["delivery_effect_pp"] / 100), ("Shorter promises", n["promise_effect_pp"] / 100)],
                              fmt_month(current), n["current_rate"], [BLUE, ORANGE]), width="stretch")
    st.caption(f"Both months re-scored against {fmt_month(base)}'s median promise per destination state. "
               "What changes against that fixed yardstick is the delivery effect; the rest comes from promises moving.")
    show_sql(p)
with w2:
    dim = st.selectbox("**2 · Order mix or segment performance?** Split by", list(M.DIMENSIONS),
                       format_func=M.DIMENSIONS.get, index=1, key="dim")
    d = decompose("late_rate", dim, base, current, filters)
    n = d.notes
    st.plotly_chart(waterfall(fmt_month(base), n["base_rate"],
                              [("Mix", n["mix_pp"] / 100), ("Performance", n["performance_pp"] / 100), ("Interaction", n["interaction_pp"] / 100)],
                              fmt_month(current), n["current_rate"], [BLUE, ORANGE, AQUA]), width="stretch")
    st.caption(f"Mix: orders shifted toward segments that were already slower. Performance: segments themselves got worse. "
               f"{n['n_pooled_into_other']} of {n['n_segments']} segments have fewer than {MIN_N} delivered orders in a month "
               "and are pooled into “Other”.")
    show_sql(d)

st.markdown(f"**Top contributors by {M.DIMENSIONS[dim].lower()}**")
top = d.data.head(10).copy()
st.dataframe(
    top.assign(**{c_: top[c_] * 100 for c_ in ["share_base", "share_current", "rate_base", "rate_current"]}),
    hide_index=True, width="stretch",
    column_config={
        "segment": M.DIMENSIONS[dim],
        "n_base": st.column_config.NumberColumn(f"Delivered {base:%b}", format="%d"),
        "n_current": st.column_config.NumberColumn(f"Delivered {current:%b}", format="%d"),
        "share_base": st.column_config.NumberColumn(f"Share {base:%b}", format="%.1f%%"),
        "share_current": st.column_config.NumberColumn(f"Share {current:%b}", format="%.1f%%"),
        "rate_base": st.column_config.NumberColumn(f"Late {base:%b}", format="%.1f%%"),
        "rate_current": st.column_config.NumberColumn(f"Late {current:%b}", format="%.1f%%"),
        "mix_pp": st.column_config.NumberColumn("Mix (pp)", format="%+.2f"),
        "performance_pp": st.column_config.NumberColumn("Performance (pp)", format="%+.2f"),
        "interaction_pp": st.column_config.NumberColumn("Interaction (pp)", format="%+.2f"),
        "total_pp": st.column_config.NumberColumn("Total (pp)", format="%+.2f"),
    },
)

# --- Breakdown with anomaly flags ------------------------------------------------

st.subheader("Segment breakdown")
bd = breakdown("late_rate", dim, base, current, filters)
bdf = bd.data.copy()
only_flagged = st.toggle(f"Show only flagged segments ({bd.notes['n_flagged']})", value=bd.notes["n_flagged"] > 0, key="only_flagged")
if only_flagged:
    bdf = bdf[bdf.flagged]
bdf["status"] = bdf.apply(lambda r: "⚑ Flagged" if r.flagged else ("Too few orders" if not r.meets_min_n else "Within noise"), axis=1)
st.dataframe(
    bdf.assign(rate_base=bdf.rate_base * 100, rate_current=bdf.rate_current * 100)[
        ["status", "segment", "n_base", "n_current", "rate_base", "rate_current", "delta_pp", "z"]],
    hide_index=True, width="stretch",
    column_config={
        "status": "Status", "segment": M.DIMENSIONS[dim],
        "n_base": st.column_config.NumberColumn(f"Delivered {base:%b}", format="%d"),
        "n_current": st.column_config.NumberColumn(f"Delivered {current:%b}", format="%d"),
        "rate_base": st.column_config.NumberColumn(f"Late {base:%b}", format="%.1f%%"),
        "rate_current": st.column_config.NumberColumn(f"Late {current:%b}", format="%.1f%%"),
        "delta_pp": st.column_config.NumberColumn("Change (pp)", format="%+.1f"),
        "z": st.column_config.NumberColumn("z", format="%.1f"),
    },
)
st.caption(f"Flagged = at least {MIN_N} delivered orders in both months, |z| ≥ {MIN_ABS_Z:g} and a change of at least "
           f"{MIN_DELTA_PP:g} percentage points. Counts are distinct orders; an order with two sellers counts for each.")
show_sql(bd)

# --- Data quality --------------------------------------------------------------------

with st.expander("Data quality for these two months"):
    q = dq((base, current))
    st.dataframe(q.data.assign(purchase_month=q.data.purchase_month.dt.date), hide_index=True, width="stretch")
    st.caption("Rows with problems are flagged, not deleted (decision D12). None of these flags changes the late flag.")
    show_sql(q)
