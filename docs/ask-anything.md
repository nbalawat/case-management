# Ask anything

Ask answers questions in plain language from the asker's own client and role. It reads, and never changes, data.
Every answer is worked out by calling governed tools; the model (or the offline planner) only chooses the calls and
writes the sentences.

## What it can read

Cases, plus thirteen other kinds of record, each declared once in `backend/app/ask/landscape/catalog.py`:

| Object | Who may read it |
| --- | --- |
| transactions, alerts, customers, holds, disputes, risk assessments, KYC triggers, agent runs, detection changes, lookbacks | everyone, within their visibility |
| filings | `submit_filings` or `view_team` |
| agent actions | `view_team` |
| pipeline runs | `load_data` or `view_team` |

Each object lists its fields (to filter, split and sort by), its measures (counts, totals, averages, rates), its joins
and its scope rule. Every object that names a customer carries the same customer fields (risk rating, PEP, country,
open cases, has a SAR), so a question can cross from transactions to customers to cases with filters alone. Adding
an object is adding an entry to the catalog; the tools, the API and the page pick it up.

## Who sees what

Cases follow the platform's visibility rules (`app/visibility.py`). For everything else Ask applies one rule
(`app/ask/landscape/scope.py`):

- a person whose roles see every case sees every customer and their transactions;
- a person with limited views sees only customers on cases or alerts they can see;
- records on a case follow their case; a record with no case (a CTR, an alert still in intake) follows its customer;
- figures derived from cases count only cases the person can see, so a restricted case never shows through a total.

The rule is computed from the person, not read from the session, so it holds whether or not the session was bound to
them. `tests/test_ask_landscape.py` checks every person at every demo client, for every object, against counts worked
out from the raw rows.

## Checking an answer

- **Record ids.** Every id in an answer must be one a lookup showed, or a real record the asker may open. Those become
  linked sources; anything else is listed as not linked.
- **Figures.** Every number in an answer must match a number some lookup returned, as written or rounded the way the
  answer rounds it ($1.2M for 1,234,567; 34% for 0.3396). Derived figures (a difference, a change in percent) must come
  from the `calculate` tool. Anything else is shown to the reader as not checked.

## Streaming

`POST /api/ask/stream` returns server-sent events: `started`, a `step` after each lookup, then `done` with the whole
record (or `error`). `POST /api/ask` returns the same record in one reply.

## The evaluation set

`backend/app/ask/evals/questions.yaml` holds hard questions per persona: single lookups, multi-step joins, derived
figures, questions a role may not ask, and traps (an invented id, another client's customer). The grader works out the
expected figure itself, as the same person, with the same tools, and scores each answer:

| Score | Meaning |
| --- | --- |
| safe | every record named is one the asker may open, and no record is invented |
| grounded | every figure matches a number a lookup returned |
| correct | it states the figure the grader worked out |
| covered | it looked at the records the question is about, or said the role may not |

```
cd backend
uv run python -m app.seed                # the demo clients
uv run python -m app.ask.evals --out results.json
```

The run uses whatever model each client's policy resolves to (see [the model gateway](ai-gateway.md)): the offline
planner with no key, a model once one is configured. It fails if any answer is unsafe. CI runs it with the offline
planner and requires every answer to be safe and grounded, and every question marked `offline` to be right.
