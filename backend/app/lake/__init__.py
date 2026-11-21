"""History on the lake: every event that crosses the bus is also kept in open table format, per client.

  base      the contract: append rows together with how far the bus has been read, list tables, read as of a moment
  tables    what is kept and how a message becomes a row
  iceberg   Apache Iceberg through whichever catalog the deployment names
  sink      the bus consumer that fills the tables, each message exactly once
"""
