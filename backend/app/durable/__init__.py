"""Durable timers on Temporal.

Without this package nothing is lost: stage clocks are checked by a sweep, when it is asked for and when work lists
load. With TEMPORAL_URL set and the worker running (`python -m app.durable.worker`), every open case with a stage
deadline has a workflow holding a timer for it, so the escalation is applied at the moment the deadline passes,
survives restarts, and does not depend on anyone opening a page. The periodic sweep (overdue requests, expiring
alerts, KYC triggers, holds, dispute deadlines) runs from a Temporal schedule.

Layout: `clocks.py` is everything that touches the database and knows nothing of Temporal; `workflows.py` is
deterministic workflow code and imports nothing from the application; `temporal.py` connects the two.
"""
