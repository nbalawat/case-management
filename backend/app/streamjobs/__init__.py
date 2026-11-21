"""Detection that runs on the stream itself: jobs for Apache Flink, rendered per client.

The application's own scenarios run in the stream consumer, a micro-batch at a time, re-reading a customer's recent
history from the database. That is right for a laptop and for moderate volumes. Past that, a rule with a time window
belongs in a stream processor, which keeps each customer's open pattern in state and scales by partition. A job here
reads a client's transaction topic and writes canonical alerts to its alerts-in topic, so the alert hub, the cases
and the audit trail neither know nor care which engine raised them.
"""
