"""The feature store: features about customers, defined once and computed the same way for training and scoring.

    model     feature sets in YAML: aggregates over windows ending at a moment, and customer attributes
    engine    point-in-time computation: a value is what could have been known at `as_of`, never later
    online    the latest values per customer, kept in the database for scoring
    store     the shipped library of sets

Open-source and cloud-agnostic by construction: definitions are files, computation is SQL plus Python that runs on
SQLite and Postgres, and the online store is a table. A deployment that already runs Feast can export the same
definitions; nothing here depends on a cloud feature store.
"""
from .engine import compute
from .model import Feature, FeatureError, FeatureSet
from .store import get, library

__all__ = ["Feature", "FeatureError", "FeatureSet", "compute", "get", "library"]
