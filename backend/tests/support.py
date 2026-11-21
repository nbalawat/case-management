"""Shared test vocabulary: the reference client most tests run as, and its people."""
import yaml

from app.domain.access import parse_policy
from app.tenants.templates import templates

T = "meridian"  # onboarded from the full-service template by the `s` fixture
TEMPLATE = "full_service_bank"
POLICY = parse_policy(yaml.safe_load(templates()[TEMPLATE].access_source), TEMPLATE)
PERSONAS = POLICY.people


def eventually(check, seconds: float = 20.0, every: float = 0.2):
    """Return check()'s first truthy result. On the Temporal engine some work
    (a new case's briefing) runs on a worker after the request returns."""
    import time
    end = time.monotonic() + seconds
    while True:
        got = check()
        if got or time.monotonic() > end:
            return got
        time.sleep(every)
