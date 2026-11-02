# Demo script (about 4 minutes)

**Before you start:** run `uv run streamlit run app/Dashboard.py` and open http://localhost:8501. The demo uses
**Replay** on the Investigate page, so no model has to run live. Close other tabs and zoom the browser to about 110%.

---

### 0:00 – 0:30 · The question

*Screen: Dashboard, top of the page.*

> "Olist is a Brazilian marketplace. In March 2018 its late-delivery rate jumped from 14% to 19%. An operations lead
> wants to know why, who contributed, and what to look at first. I built a tool that answers that with an AI agent
> whose every number can be checked."

### 0:30 – 1:30 · The dashboard: two explanations people mix up

*The sidebar already shows March 2018 vs February 2018. Point at the KPI tiles, then scroll to the two waterfalls.*

> "First, the basics. One row per order, tested against the source, because a careless join overstates revenue by
> 28%. And only complete months, because recent months look on time while late orders are still on the road."

*Point at the promise-vs-speed waterfall.*

> "'Late' means delivered after the promised date. So the rate can rise because deliveries got slower, or because
> the promise got shorter. Here, about 70% of the rise is shorter promises. Actual delivery times barely moved."

*Point at the mix-vs-performance waterfall, then the flagged table.*

> "And it's not mix. Orders didn't shift toward slow regions; the regions themselves got worse. A segment is only
> flagged with at least 30 orders in both months, a z-score of 2 and a 3-point change. These rules are written on the
> page."

### 1:30 – 2:45 · The agent investigates

*Investigate page → Replay → choose "2018-03 vs 2018-02" → Replay.*

> "Now the agent. It's a free local model with four read-only tools. It never writes SQL and never does arithmetic.
> It decides what to look at next: first data quality, then the overall comparison, then break-downs by region."

*Let the timeline finish. Point at the metrics row, then a blue E-badge, then open that evidence panel and its SQL.*

> "Every number carries a citation. Each citation opens the tool call, the result and the exact SQL. A checker
> verifies every number against the evidence it cites; here it's 20 out of 20. If the agent skips a step or a number
> doesn't match, the code sends it back. That's not left to the prompt."

*Switch the replay to "2016-10 vs 2016-09" → Replay.*

> "And when the data isn't good enough, here the first months of the business with a handful of orders, it says
> 'insufficient evidence' instead of inventing a story."

### 2:45 – 3:45 · Does it actually beat a plain AI summary?

*Evaluation page. Point at the headline tiles, then the accuracy-by-fact chart.*

> "To check that this is better than asking an AI to summarize the dashboard, I wrote 25 questions and computed the
> answers a second way, directly on the raw tables. Same model for both. The agent gets 92% of the facts right
> against 59%, and every one of its 427 numbers traces to the data. Both read the headline numbers fine. The
> difference is the 'why': noise or not, promises or deliveries, which states. And it said 'insufficient
> evidence' every time it should have. Its weak spot is judgement: it often says 'both' instead of naming the
> bigger effect. That's my next fix."

*Pick a "fall" question in the selector and show both answers side by side.*

> "Every question asks why the rate *increased*, even when it fell. That tests whether the system checks the premise."

### 3:45 – 4:15 · Close

> "Everything is free and runs on a laptop: DuckDB, Streamlit and an open model. The repo has the tests, the decision
> log and a one-page case study. The main lesson: let the code compute and the model explain, and measure the
> result."

---

**Likely questions**

- *Why not let the model write SQL?* Fixed tools make every number reproducible and testable; free-form SQL is where
  small models make silent mistakes.
- *How do you know the answer key is right?* It's a second implementation on the raw staging tables, and a test
  requires it to agree with the tools on all 19 complete-month questions.
- *Correlation or causation?* Association. The report says so, and "investigate first" lists things to check, not
  proven causes.
- *Why a local model?* Free and private. The provider layer swaps in Claude with one setting.
