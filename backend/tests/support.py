"""Shared test vocabulary: the reference client most tests run as, and its people."""
import yaml

from app.domain.access import parse_policy
from app.tenants.templates import templates

T = "meridian"  # onboarded from the full-service template by the `s` fixture
TEMPLATE = "full_service_bank"
POLICY = parse_policy(yaml.safe_load(templates()[TEMPLATE].access_source), TEMPLATE)
PERSONAS = POLICY.people
