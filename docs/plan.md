# Project plan: AI Business Investigation Analyst

**Business question:** "Why did the late-delivery rate go up this month? Which
sellers, regions or product categories contributed most, and what should we
investigate first?"

**Constraints:** completely free, runs locally, built step by step so both
builders learn the skills (not copy-paste).

## Stack (all free)
| Layer | Tool |
|---|---|
| Data | Olist Brazilian E-Commerce (Kaggle, free account) |
| Storage + SQL | DuckDB (a single local database file) |
| Analysis | Python, pandas |
| Dashboard | Streamlit + Plotly |
| Agent model | Ollama running `qwen3:8b` locally; swappable (see decisions D9) |
| Tests | pytest |
| Hosting the code | GitHub |

## Roles
- **Analyst** (SQL and analysis lead): writes the SQL metric queries, the
  manual investigation, the anomaly rules and the case study. Gets an
  explanation of each concept and its trap first, writes the query, then gets
  a review with tests run against it.
- **Engineer** (Python and agent lead): writes the data loader, the tool
  functions, the LLM layer, the agent loop and the app wiring, with guidance.
- **Both:** evaluation questions. One person writes a question, the other
  writes its SQL answer independently.

## Stages and done-criteria

### Stage 1: Validated data model and SQL metrics
- [ ] Profile the raw CSVs: row counts, keys, nulls, date ranges, multi-row orders
- [ ] Load the raw tables into DuckDB (`src/load.py`)
- [ ] Clean tables and `fct_orders`, one row per order (`sql/`)
- [ ] Metric definitions written down: late rate, order value, low-review rate, cancellation rate
- [ ] Decide which months are complete enough to analyze (decision D3)
- [ ] Tests: order count and payment total match the source after every join
- **Done when:** `uv run pytest` passes and every *(to verify)* in `decisions.md` is resolved.

### Stage 2: Dashboard and one manually verified investigation
- [ ] Streamlit page: KPI tiles, monthly trend, breakdown by seller / state / category, filters
- [ ] Anomaly flags with minimum sample size (decision D6)
- [ ] Mix vs performance decomposition in SQL (decision D5)
- [ ] One investigation written by hand: pick a month, decompose the change, write the conclusion
- **Done when:** the hand-written investigation's numbers match the dashboard exactly.

### Stage 3: Agent with linked evidence
- [ ] Four read-only tools: `compare_periods`, `break_down_metric`, `inspect_orders`, `check_data_quality`
- [ ] `src/llm.py` provider layer (Ollama default)
- [ ] Agent loop: the model picks the next tool based on earlier results; every call is logged with its SQL and result
- [ ] Report: findings, numbers linked to evidence, association vs causation, limitations, "insufficient evidence" when appropriate
- [ ] Replay mode: saved runs play back in the app with no model running
- **Done when:** the agent reproduces the Stage 2 manual investigation's main finding, with every number cited.

### Stage 4: Evaluation and portfolio polish
- [ ] 20–30 questions with independently verified SQL answers, including missing-data and insufficient-evidence cases
- [ ] Metrics: numerical accuracy, evidence support, tool failures, response time, cost ($0 locally)
- [ ] Baseline: the same model summarizing dashboard numbers without tools
- [ ] README (setup + architecture), one-page case study, 3–5 minute demo script
- **Done when:** a new user can clone the repo, follow the README, and see the dashboard and a replayed investigation.

## Out of scope for the MVP
Deployment, user logins, live data, multiple pages, forecasting.

## One-time setup still needed
1. Download the Olist dataset from Kaggle and unzip into `data/raw/`.
2. (Before Stage 3) `ollama pull qwen3:8b` (~5 GB download).
