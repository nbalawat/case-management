"""Case workflows on Temporal (WORKFLOW_ENGINE=temporal).

Each open case is a `CaseWorkflow`: people's steps reach it as workflow updates and are applied one at a time by an
activity that runs the case type's checks; its stage deadline is a durable timer; anything else that moves the case
tells it to look again. Each client has its own namespace (or a shared one, TEMPORAL_NAMESPACE_MODE=shared) with
schedules for the periodic sweep and for starting due data pipelines. Without Temporal the built-in engine
(app.workflow) does the same work inside the application, and the sweep checks deadlines.

Layout: `steps.py` and `clocks.py` touch the database and know nothing of Temporal; `workflows.py` is deterministic
workflow code and imports nothing from the application; `temporal.py` connects the two; `engine.py` is the engine the
API calls; `namespaces.py` keeps clients apart; `bridge.py` lets synchronous code call the asyncio client; `worker.py`
runs it all.
"""
