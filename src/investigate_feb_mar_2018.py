"""The manual investigation: why did the late rate rise from Feb to Mar 2018?

    uv run python -m src.investigate_feb_mar_2018

Writes docs/investigation_2018_03.md. The narrative is written by hand, but
every number in it is filled in from src/metrics.py at build time and tagged
with an evidence ID [E#] whose query is listed at the end. This is the
standard the agent's reports must meet in Stage 3.
"""

import datetime as dt

from src import metrics as M
from src.config import MIN_N, ROOT
from src.db import connect

FEB, MAR = dt.date(2018, 2, 1), dt.date(2018, 3, 1)


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def pp(x, d=1):
    return f"{x:+.{d}f} pp"


def main() -> None:
    con = connect()
    ev = []  # (id, description, Result)

    def cite(desc, result):
        ev.append((f"E{len(ev) + 1}", desc, result))
        return f"[E{len(ev)}]"

    cmp_ = M.compare_periods(con, "late_rate", FEB, MAR)
    c = cmp_.data.iloc[0]
    e_cmp = cite("compare_periods(late_rate, 2018-02, 2018-03)", cmp_)

    dq = M.data_quality(con, [FEB, MAR])
    q = dq.data.set_index(dq.data.purchase_month.dt.date)
    e_dq = cite("data_quality(2018-02, 2018-03)", dq)

    mixes = {d: M.decompose_mix(con, "late_rate", d, FEB, MAR) for d in M.DIMENSIONS}
    e_mix = {d: cite(f"decompose_mix(late_rate, {d})", r) for d, r in mixes.items()}

    prom = M.promise_vs_speed(con, FEB, MAR)
    p = prom.notes
    pdf = prom.data.set_index("period")
    e_prom = cite("promise_vs_speed(2018-02, 2018-03)", prom)

    wk = M.weekly(con, dt.date(2018, 1, 29), dt.date(2018, 4, 9))
    w = wk.data.set_index("week")
    e_wk = cite("weekly(2018-01-29 to 2018-04-08)", wk)
    peak_week = w.late_rate.idxmax()
    peak_actual_week = w.avg_delivery_days.idxmax()

    by_state = M.break_down(con, "late_rate", "customer_state", FEB, MAR)
    bs = by_state.data
    e_state = cite("break_down(late_rate, customer_state)", by_state)
    flagged_states = bs[bs.flagged].sort_values("n_current", ascending=False)

    by_cat = M.break_down(con, "late_rate", "category", FEB, MAR)
    e_cat = cite("break_down(late_rate, category)", by_cat)
    n_cat_flagged, n_cat_eligible = by_cat.notes["n_flagged"], int(by_cat.data.meets_min_n.sum())

    mg = M.promise_vs_speed(con, FEB, MAR, {"customer_state": "MG"})
    e_mg = cite("promise_vs_speed(2018-02, 2018-03, customer_state=MG)", mg)
    mgd = mg.data.set_index("period")

    rev = M.compare_periods(con, "low_review_rate", FEB, MAR).data.iloc[0]
    e_rev = cite("compare_periods(low_review_rate, 2018-02, 2018-03)", M.compare_periods(con, "low_review_rate", FEB, MAR))

    ms = mixes["seller_state"].notes
    mc = mixes["customer_state"].notes
    mcat = mixes["category"].notes
    msel = mixes["seller_id"].notes
    biggest_mix_dim = max(mixes, key=lambda d: abs(mixes[d].notes["mix_pp"]))
    biggest_mix = abs(mixes[biggest_mix_dim].notes["mix_pp"])
    promise_share = p["promise_effect_pp"] / p["delta_pp"]

    # The narrative below makes these claims in words; fail loudly if the data
    # ever stops supporting them.
    assert all(abs(m.notes["mix_pp"]) < 1 for m in mixes.values()), "claim: not a mix effect"
    assert p["promise_effect_pp"] > p["delivery_effect_pp"] > 0, "claim: mostly shorter promises"
    assert (q.loc[[FEB, MAR], ["delivered_without_date", "carrier_before_purchase", "delivered_before_carrier"]] == 0).all().all(), \
        "claim: no timestamp anomalies"
    assert q.loc[[FEB, MAR], "is_complete"].all(), "claim: both months complete"
    assert mgd.loc["base", "median_delivery_days"] == mgd.loc["current", "median_delivery_days"], "claim: MG not slower"
    assert pdf.loc["current", "avg_delivery_days"] < pdf.loc["base", "avg_delivery_days"], "claim: average delivery time fell"

    def wrow(day):
        r = w.loc[day]
        return f"| {day:%d %b} | {int(r.delivered):,} | {pct(r.late_rate)} | {r.avg_promised_days:.1f} | {r.avg_delivery_days:.1f} |"

    state_rows = "\n".join(
        f"| {r.segment} | {r.n_base:,} → {r.n_current:,} | {pct(r.rate_base)} → {pct(r.rate_current)} | {pp(r.delta_pp)} | {r.z:.1f} |"
        for r in flagged_states.itertuples())

    doc = f"""# Investigation: why did late deliveries rise in March 2018?

> **Simulation: historical replay.** Olist data; "this month" is March 2018,
> compared with February 2018. Written by hand as the reference for the agent.
> Every number is generated from `src/metrics.py` and carries an evidence ID;
> the queries are listed at the end. Regenerate with
> `uv run python -m src.investigate_feb_mar_2018`.

## Answer in brief

The late-delivery rate rose from **{pct(c.base)} to {pct(c.current)}** ({pp(c.delta_pp)}, z = {c.z:.1f}) {e_cmp}.
The rise is **not a mix effect**: under every split we tried, orders did not
shift toward slower segments (the largest mix effect is {biggest_mix:.1f} pp, by {M.DIMENSIONS[biggest_mix_dim].lower()}) {e_mix[biggest_mix_dim]}.
It was broad-based, and {promise_share:.0%} of it lines up with **shorter
delivery promises**, not slower deliveries:

- Judged against a fixed yardstick (February's typical promise per destination
  state), deliveries got slower by only **{pp(p['delivery_effect_pp'])}**; the remaining
  **{pp(p['promise_effect_pp'])}** came from estimated delivery dates moving closer {e_prom}.
- The median promise fell from **{pdf.loc['base', 'median_promised_days']:.0f} to {pdf.loc['current', 'median_promised_days']:.0f} days** while the median actual
  delivery time went from {pdf.loc['base', 'median_delivery_days']:.0f} to {pdf.loc['current', 'median_delivery_days']:.0f} days {e_prom}.

**Investigate first:** what changed in how estimated delivery dates were set
from mid-February 2018, and whether the shorter promises were intended.
Second: the delivery slowdown that peaked in the week of {peak_actual_week:%d %B}.

## 1. Is the data trustworthy for these months?

Both months are complete under our rules {e_dq}: {q.loc[FEB, 'n_delivered']:,} and
{q.loc[MAR, 'n_delivered']:,} delivered orders, no estimates beyond the data cutoff, and
no delivery-timestamp anomalies. One thing to keep in mind: orders that were
never delivered after their estimate passed rose from {q.loc[FEB, 'overdue_undelivered']} to
{q.loc[MAR, 'overdue_undelivered']} {e_dq}. They are not in the late rate (no delivery date), so if
anything the March rate **understates** the problem.

## 2. Is the change real?

{c.base_x:,} of {c.base_n:,} delivered orders were late in February and
{c.current_x:,} of {c.current_n:,} in March {e_cmp}. A z-score of {c.z:.1f} is far beyond
the noise threshold of 2, so this is not random month-to-month variation.

## 3. Mix or performance?

| Split by | Mix | Performance | Interaction | Evidence |
|---|---|---|---|---|
| Customer state | {pp(mc['mix_pp'], 2)} | {pp(mc['performance_pp'], 2)} | {pp(mc['interaction_pp'], 2)} | {e_mix['customer_state']} |
| Seller state | {pp(ms['mix_pp'], 2)} | {pp(ms['performance_pp'], 2)} | {pp(ms['interaction_pp'], 2)} | {e_mix['seller_state']} |
| Product category | {pp(mcat['mix_pp'], 2)} | {pp(mcat['performance_pp'], 2)} | {pp(mcat['interaction_pp'], 2)} | {e_mix['category']} |
| Seller | {pp(msel['mix_pp'], 2)} | {pp(msel['performance_pp'], 2)} | {pp(msel['interaction_pp'], 2)} | {e_mix['seller_id']} |

Under every split, almost the whole change is **performance**: segments got
worse in place, rather than volume moving toward segments that were already
slow. (At seller level, {msel['n_pooled_into_other']:,} of {msel['n_segments']:,} sellers have fewer than {MIN_N}
delivered orders in a month and are pooled into "Other", so that row says
little about individual sellers.)

The deterioration was also widespread: {len(flagged_states)} customer states pass all three
anomaly rules {e_state}, and {n_cat_flagged} of the {n_cat_eligible} categories with enough orders
are flagged too {e_cat}. A problem that hits many destinations and product types
at once points to something shared (estimates, carriers), not to a few bad
sellers.

| Flagged customer state | Delivered orders (Feb → Mar) | Late rate | Change | z |
|---|---|---|---|---|
{state_rows}

## 4. Slower deliveries, or shorter promises?

An order is late when actual delivery days exceed promised days, so the late
rate can rise for either reason. The two months tell different stories {e_prom}:

| | Feb 2018 | Mar 2018 |
|---|---|---|
| Average promised days | {pdf.loc['base', 'avg_promised_days']:.1f} | {pdf.loc['current', 'avg_promised_days']:.1f} |
| Average actual days | {pdf.loc['base', 'avg_delivery_days']:.1f} | {pdf.loc['current', 'avg_delivery_days']:.1f} |
| Late rate | {pct(p['base_rate'])} | {pct(p['current_rate'])} |
| Late rate against Feb's promise per state | {pct(pdf.loc['base', 'late_rate_same_yardstick'])} | {pct(pdf.loc['current', 'late_rate_same_yardstick'])} |

Average actual delivery time *fell* slightly, yet the late rate rose, because
promises shrank faster. Holding the promise fixed explains only
{pp(p['delivery_effect_pp'])} of the {pp(p['delta_pp'])} change.

**Clearest single example, Minas Gerais (MG):** the median promise went from
{mgd.loc['base', 'median_promised_days']:.0f} to {mgd.loc['current', 'median_promised_days']:.0f} days, the median delivery time from {mgd.loc['base', 'median_delivery_days']:.0f} to {mgd.loc['current', 'median_delivery_days']:.0f} days, and the
late rate from {pct(mgd.loc['base', 'late_rate'])} to {pct(mgd.loc['current', 'late_rate'])} {e_mg}. Deliveries to MG didn't get slower;
the promise did get shorter.

## 5. The month boundary hides the shape of the episode

Weekly, by purchase week {e_wk}:

| Week of | Delivered | Late rate | Promised days | Actual days |
|---|---|---|---|---|
{chr(10).join(wrow(d) for d in w.index)}

The promise dropped in mid-February and **stayed lower**. On top of that,
deliveries slowed for a few weeks: actual time peaked at {w.avg_delivery_days.max():.1f} days in the week of
{peak_actual_week:%d %B}, and the late rate peaked at {pct(w.late_rate.max())} in the week of {peak_week:%d %B}.
By April, deliveries were faster than before, so the shorter promise stopped
causing misses. In other words, the shorter promise left no buffer, and a
temporary slowdown turned into a spike in late deliveries. Comparing whole
months splits this one episode across February and March.

## 6. Did customers notice?

The low-review rate (1–2 stars) rose from {pct(rev.base)} to {pct(rev.current)} ({pp(rev.delta_pp)}, z = {rev.z:.1f}) {e_rev}.
That is consistent with late deliveries hurting satisfaction, but this
comparison alone doesn't prove the lateness caused it.

## Recommended next steps

1. **Review how estimated delivery dates were calculated from mid-February
   2018.** Find out whether the shorter promises were a deliberate change (for
   example, to improve conversion) and whether they were tested against actual
   delivery times.
2. **Look into the late-February delivery slowdown** with carriers, starting
   with the flagged destination states above.
3. **Monitor slack** (promised days minus actual days) next to the late rate,
   so a shrinking buffer shows up before the late rate does.

## Limitations

- **Association, not causation.** The data shows that promises shortened and
  lateness rose together. It can't tell us why the estimates changed, or
  whether something else changed at the same time.
- The "fixed yardstick" uses February's *median* promise per state; a
  different yardstick would split the change somewhat differently, though it
  would not change the conclusion that promises moved more than deliveries.
- {int(q.loc[MAR, 'overdue_undelivered'])} March orders were never delivered after their estimate passed, and
  they are outside the late rate.
- No revenue or savings figures are given; the data doesn't support them.

## Evidence

Each ID is a call to `src/metrics.py`; the SQL below is exactly what ran.
"""
    for eid, desc, r in ev:
        doc += f"\n### {eid}: `{desc}`\n\n```sql\n{r.sql_with_params().strip()}\n```\n"

    path = ROOT / "docs" / "investigation_2018_03.md"
    path.write_text(doc)
    print(f"wrote {path} ({len(ev)} evidence items)")


if __name__ == "__main__":
    main()
