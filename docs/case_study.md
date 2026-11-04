# Case study: investigating a late-delivery spike with an evidence-checked AI agent

**Savitha Sundaresan** · Python, SQL (DuckDB), Streamlit, local LLM with tool calling ·
[Code](https://github.com/savithasundaresan/ai-investigation-analyst)

## The problem

In March 2018, Olist's late-delivery rate rose from 14.1% to 19.0%. An operations lead asks the usual questions: *why
did it go up, who contributed, and what should we look at first?* Answering usually takes an analyst a day of
slicing, and a dashboard alone shows *that* it went up, not *why*.

## What I built

1. **A data model you can trust.** Raw tables → one row per order → marts, in SQL. A naive payments-to-items join
   overstates revenue by 28% (R$20.31M vs R$15.85M). Tests check every total against the source after each join.
   Months are only analyzed when complete, because recent months look on time while their late orders are still in
   transit.
2. **A dashboard with rules anyone can read.** A segment is flagged only with ≥30 orders in both months, |z| ≥ 2 and
   a change of ≥3 pp.
3. **Two decompositions that separate the explanations people usually mix up.**
   - *Mix vs performance:* did orders move to regions that were already slow, or did regions get slower?
   - *Slower deliveries vs shorter promises:* "late" means past the promised date, so the rate rises if deliveries
     slow down **or** if the promise gets shorter.
4. **An AI agent that investigates and shows its work.** A local model (free, `qwen3:8b`) chooses which of four
   read-only tools to call. It never writes SQL or does arithmetic. Each tool result becomes evidence `E1, E2, …`
   with its SQL, and a checker verifies every number in the report against the evidence it cites. Guardrails in code
   send the agent back if it skips steps and refuse rates for incomplete months.
5. **An evaluation that could have proven me wrong.** 25 questions with answers from an independent SQL answer key,
   comparing the agent against the same model summarizing the dashboard.

## What the data said

- **69% of the March rise came from shorter delivery promises.** Average promised time fell from 25.2 to 22.7 days
  while actual delivery time *improved* slightly (16.9 → 16.2 days). Judged against February's promise, deliveries
  explain only 1.5 of the 4.84 pp.
- **Performance, not mix:** under every split, segments got worse in place; the largest mix effect is 0.4 pp.
- **Broad, not local:** 6 customer states and 7 categories were flagged, which points to something shared, such as
  how delivery estimates were set, rather than a few bad sellers.
- **Recommendation:** first review what changed in the delivery-estimate logic from mid-February 2018 and whether the
  shorter promises were intended. These are associations; the data can't prove the cause.
- Black Friday 2017 is the opposite case: the whole +8.2 pp rise was slower deliveries.

## Did the agent beat a plain AI summary?

Yes, on everything beyond the headline numbers. Both systems used the same free local model on 25 questions. The
expected answers came from an answer key I wrote a second time, directly on the raw tables, and a test checks that it
agrees with the agent's tools.

| | Agent | Plain AI summary |
|---|---|---|
| Facts correct | **92%** | 59% |
| Questions fully correct | **15 / 25** | 1 / 25 |
| Numbers traceable to the data | **100%**, each correctly cited | 94% |
| Said "insufficient evidence" for bad months | **6 / 6** | 3 / 6 |
| Median time | 80 s | 19 s |

The trap questions mattered most. Every question asked why the rate *increased*, including months where it fell or
barely moved, plus months with too little data. The plain summary treated all 4 noise-level changes as real, called 2 real changes noise,
and reported late rates for months it should not have. The agent's main weakness is judgement: it names the larger of two
effects only 9 times in 15, often saying "both".

## What I'd do next

- Fix the measured weakness: have `compare_periods` return which effect is larger, so the model copies a label
  instead of judging.
- Try a larger model through the same provider layer and compare on the same 25 questions.
- Add carrier and shipping-limit data, so "investigate first" can point at a carrier rather than a region.

## What I learned

- **Definitions are the work.** "Late", "complete month" and "order value" each had a wrong-but-plausible version;
  writing them down with alternatives ([decisions.md](decisions.md)) made the analysis defensible.
- **Make the model do less.** The agent became reliable when the code computed every number and the model only chose
  the next step and wrote prose.
- **Measure the thing you claim.** The evaluation found weaknesses I would otherwise have missed, and the independent
  answer key would have caught a bug in the shared metric code.
