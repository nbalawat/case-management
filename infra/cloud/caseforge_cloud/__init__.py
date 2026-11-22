"""The CaseForge cloud machine: one command to set an account up, bring the whole stack up, stop, start and tear down.

Runs on Python 3.9 or later with the standard library only (macOS's own python3 is enough), plus OpenTofu and an
OpenSSH client. Entry point: infra/bin/cloud.py.
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
