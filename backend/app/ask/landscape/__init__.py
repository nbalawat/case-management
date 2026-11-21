"""The whole case landscape as a governed query layer: every domain object, not just cases.

An answering agent never writes SQL. It names an object (transactions, alerts, customers, filings, holds,
disputes, KYC risk assessments, agent actions and more), a few fields to group or filter by and the measures it
wants, from a fixed vocabulary. This package compiles that into one SQL statement that runs in the asker's own
client session, under the asker's visibility, and returns the rows with example records for each figure.

  scope    what this person may see: cases, customers, and everything that hangs off them
  query    the vocabulary types (fields, measures, objects) and the compiler and runner
  catalog  the objects themselves
"""
