"""What differs between one FIU's goAML and another's: local currency, code lists, and which report codes exist.

A client names its FIU's profile in its filing institution details (`goaml_profile`, default lu-crf) and the schema file
(`goaml_schema`). A profile's fields can be overridden per client from code (`institution["goaml"]`), for example to
add exchange rates; there is no screen for that yet. The values below follow goAML's commonly used lookup tables. Each FIU
publishes its own; confirm them against that list before a client files for real.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace


@dataclass(frozen=True)
class Profile:
    id: str
    title: str
    currency: str
    transmode: dict[str, str]  # our channel -> conduction_type
    transmode_other: str
    funds: dict[str, str]  # our channel -> funds_type
    funds_other: str
    indicators: dict[str, str]  # our detection scenario -> report indicator
    indicator_other: str
    id_types: dict[str, str]  # our identifier kind -> identifier_type, for the identification block
    contact_private: str = "1"
    contact_business: str = "2"
    communication_landline: str = "L"
    account_type_current: str = "A"
    role_holder: str = "A"
    status_active: str = "A"
    status_closed: str = "C"
    cash_report_code: str = "CASH"
    reason_kinds: tuple[str, ...] = ("STR", "SAR", "CASH")  # report codes that carry a reason (CRF rule REJ-REA02)
    placeholder_bic: str = "XXXXXXXX"  # used only until the client's own BIC is set
    rates: dict[str, float] = field(default_factory=dict)  # currency -> local currency per unit


PROFILES: dict[str, Profile] = {p.id: p for p in (
    Profile("lu-crf", "Luxembourg, Cellule de renseignement financier (goAML 5.2, schema 5.0.2)", "EUR",
            transmode={"cash": "A", "wire": "B", "ach": "B", "card": "C", "p2p": "D"}, transmode_other="K",
            funds={"cash": "A", "wire": "K", "ach": "K", "card": "F", "p2p": "K"}, funds_other="-",
            indicators={"structuring": "002TS-TxnSmurfing", "rapid_movement": "002TS-TransitAcct", "high_risk_geo": "002TS-TxnRiskCountries",
                        "sanctions_name": "002TS-SanctionsList", "fraud": "002TS-TxnFraud"},
            indicator_other="002TS-TxnMonitoring",
            id_types={"passport": "A", "national_id": "B", "drivers_license": "C", "alien_registration": "D"},
            rates={"USD": 0.92, "GBP": 1.17, "CHF": 1.05}),
)}
DEFAULT = "lu-crf"


def profile_for(institution: dict) -> Profile:
    """The FIU profile a client names, with any of its own overrides (rates, BIC placeholder, codes) applied."""
    base = PROFILES.get(str(institution.get("goaml_profile") or DEFAULT), PROFILES[DEFAULT])
    own = institution.get("goaml") or {}
    allowed = {f.name for f in fields(Profile)} - {"id", "title"}
    changes = {k: (base.__dict__[k] | v if isinstance(v, dict) else v) for k, v in own.items() if k in allowed}
    return replace(base, **changes) if changes else base
