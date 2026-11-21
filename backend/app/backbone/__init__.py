"""The event backbone: domain events out, transactions and alerts in.

  outbox    every audit event and every new alert becomes a CloudEvent row in the same database transaction
  bus       the port the rest of this package is written against, with an in-process adapter
  kafka     the adapter for Kafka and anything that speaks its protocol
  relay     moves outbox rows onto the bus, at least once and in order per key
  streams   consumes each client's transaction and alert topics into the same loaders the pipelines use
  worker    runs the relay and the consumers, inside the API process or beside it
"""
