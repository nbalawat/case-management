"""A repeatable load test of the real ingestion path, with a report of what was measured on this machine.

    python -m app.loadtest run --transactions 200000            against the configured bus, database and lake
    python -m app.loadtest run --transactions 20000 --inline    consumers in this process (no worker needed)

Stages: set up a load-test client and its customers, publish synthetic transactions to the client's transactions
topic, wait until the stream consumers have loaded (and run detection on) every one, wait until the lake has them,
time a set of lake queries, then put read load on the API. Each stage's numbers are measured; the report's
projection toward the design target is arithmetic on those numbers and says so.
"""
