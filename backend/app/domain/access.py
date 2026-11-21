"""Access policy loaded from configuration: permissions, queues, roles and the people who hold them.

The policy file is the single source of truth. Code asks the policy; it never names a role.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml


class AccessPolicyError(ValueError):
    pass


SCOPES = ("all", "queues", "own")
RESTRICTED_VIEW, RESTRICT = "view_restricted", "restrict_case"
# People from the platform provider's own staff who work this client's cases under a managed-service engagement.
# Their ids carry this prefix so they can never collide with, or be mistaken for, the client's own people.
PROVIDER_PREFIX = "ms."
CLIENT, PROVIDER = "client", "provider"
# Permissions a client can never hand to the provider's staff: control over who has access stays with the client.
NEVER_DELEGATED = frozenset({"manage_access", "certify_access"})
SIGNOFF_STEPS = {"four_eyes": "Four-eyes approvals of work done by the managed-service team",
                 "submit_filings": "Sending regulatory reports"}
APPROVALS = ("client", "none")


@dataclass(frozen=True)
class Sees:
    """Which cases a role can see. Absent from a role, the role sees every case at the client.

    scope: all    every case of the listed types
           queues cases in a queue the person belongs to
           own    only cases the person owns
    Whatever the scope, people keep sight of cases they own or have acted on, so history and four-eyes still work.
    Restricted cases are a separate matter: they need the view_restricted permission, whatever a role sees.
    """
    scope: str = "all"
    case_types: frozenset[str] | None = None  # None: every case type

    def describe(self, titles: dict[str, str] | None = None) -> str:
        if self.scope == "own":
            return "Only cases they own or have worked on"
        where = " in their own queues" if self.scope == "queues" else ""
        if self.case_types is None:
            return f"Every case{where}"
        return f"{', '.join(sorted((titles or {}).get(t, t) for t in self.case_types))} cases{where}"


@dataclass(frozen=True)
class Customers:
    """Which customers a role reaches, by their attributes. Absent from a role, the role reaches every customer.

    jurisdictions  where the relationship is booked (the customer's country when no booking entity is recorded)
    segments       retail, business, private, correspondent... as the client's data names them
    book           only customers whose relationship manager is the person themself
    A customer out of reach does not exist for the person, and neither do its accounts, transactions, alerts, cases,
    documents and reports. Unlike case scope, owning or having worked a case does not lift it: these are hard lines.
    """
    jurisdictions: frozenset[str] | None = None
    segments: frozenset[str] | None = None
    book: bool = False

    @property
    def everyone(self) -> bool:
        return self.jurisdictions is None and self.segments is None and not self.book

    def narrowed(self, jurisdictions: frozenset[str] | None, segments: frozenset[str] | None) -> "Customers":
        """The same reach, limited further by a person's own attributes."""
        def meet(a, b):
            return b if a is None else a if b is None else a & b
        return Customers(meet(self.jurisdictions, jurisdictions), meet(self.segments, segments), self.book)

    def describe(self) -> str:
        if self.everyone:
            return "Every customer"
        parts = ["their own book" if self.book else "customers"]
        if self.segments is not None:
            parts.append(f"in {', '.join(sorted(self.segments)) or 'no'} segment{'s' if len(self.segments) != 1 else ''}")
        if self.jurisdictions is not None:
            parts.append(f"booked in {', '.join(sorted(self.jurisdictions)) or 'nowhere'}")
        text = " ".join(parts)
        return text[0].upper() + text[1:]


ALL_CUSTOMERS = Customers()


@dataclass(frozen=True)
class TippingOff:
    """What customer-facing people must never learn, so they cannot tip a customer off.

    Cases of these types (where suspicion is investigated) and every regulatory report are hidden from anyone holding a
    customer-facing role, even for their own customers.
    """
    case_types: frozenset[str] = frozenset({"tm_alert_investigation"})
    filings: bool = True  # hide regulatory reports from customer-facing people


@dataclass(frozen=True)
class BreakGlass:
    """Emergency access: someone with the break_glass permission opens one case or customer they cannot otherwise see,
    for a stated reason, for a few hours. Every opening is recorded and reviewed afterwards by someone else."""
    hours: int = 4
    restricted: bool = False  # whether restricted cases can be opened this way


BREAK_GLASS = "break_glass"


@dataclass(frozen=True)
class Role:
    id: str
    title: str
    permissions: frozenset[str]
    read_only: bool = False
    sees: Sees | None = None
    customers: Customers | None = None  # None: every customer
    customer_facing: bool = False  # relationship managers and others who speak to customers: tipping-off rules apply


@dataclass(frozen=True)
class ManagedService:
    """Whether, and on what terms, the platform provider's staff may work this client's cases.

    Absent from a policy, nobody from the provider can be given a place at the client: the secure default.
    """
    provider: str
    roles: frozenset[str]  # the roles provider staff may be given; never one that controls access
    approval: str = "client"  # client: each engagement waits for someone here with manage_access. none: starts at once
    max_days: int = 180  # longest engagement; it ends by itself after this
    client_signoff: frozenset[str] = frozenset()  # steps a provider maker needs a client checker for (SIGNOFF_STEPS)

    def describe(self) -> list[str]:
        out = [f"{self.provider} staff may hold: {', '.join(sorted(self.roles))}",
               "Each engagement needs approval here" if self.approval == "client" else "Engagements start without approval here",
               f"Engagements last at most {self.max_days} days"]
        out += [f"Client sign-off: {SIGNOFF_STEPS[x].lower()}" for x in sorted(self.client_signoff)]
        return out


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    title: str
    queues: tuple[str, ...] = ()
    about: str = ""
    permissions: frozenset[str] = frozenset()
    read_only: bool = False
    roles: tuple[str, ...] = ()  # every role held; `role` is the first and is the one shown
    # What this person can see, one entry per role that limits it. None: at least one role sees everything.
    views: tuple[Sees, ...] | None = None
    org: str = CLIENT  # provider: a member of the platform provider's staff, here under an engagement
    engagement: str | None = None  # the engagement that gives a provider person their place
    # Which customers this person reaches, one entry per role, already narrowed by their own attributes. None: all.
    customers: tuple[Customers, ...] | None = None
    customer_facing: bool = False

    @property
    def external(self) -> bool:
        return self.org == PROVIDER

    def holds(self, roles) -> bool:
        return any(r in roles for r in (self.roles or (self.role,)))

    def can(self, permission: str) -> bool:
        """Read-only roles may look (view_*) but hold no permission that changes anything."""
        return permission in self.permissions and (not self.read_only or permission.startswith("view_"))


@dataclass(frozen=True)
class AccessPolicy:
    name: str
    version: str
    permissions: dict[str, str]
    queues: dict[str, str]
    roles: dict[str, Role]
    people: dict[str, Persona]
    default_person: str
    managed_service: ManagedService | None = None
    tipping_off: TippingOff = TippingOff()
    break_glass: BreakGlass | None = None

    @property
    def own_people(self) -> dict[str, Persona]:
        """The client's own people, without anyone here under a managed-service engagement."""
        return {k: p for k, p in self.people.items() if not p.external}

    def with_people(self, extra: dict[str, Persona]) -> "AccessPolicy":
        return replace(self, people=self.own_people | extra)

    def person(self, pid: str | None) -> Persona:
        return self.people.get(pid or "", self.people[self.default_person])

    def queue_members(self, queue: str | None) -> list[Persona]:
        return [p for p in self.people.values() if queue in p.queues]

    def holders(self, role: str) -> list[Persona]:
        return [p for p in self.people.values() if p.holds((role,))]


def parse_policy(raw: dict, origin: str = "access policy") -> AccessPolicy:
    def fail(msg: str):
        raise AccessPolicyError(f"{origin}: {msg}")

    if not isinstance(raw, dict):
        fail("must be a YAML mapping")
    for key in ("permissions", "queues", "roles", "people"):
        if not raw.get(key):
            fail(f"missing '{key}'")
    permissions, queues = dict(raw["permissions"]), dict(raw["queues"])
    roles: dict[str, Role] = {}
    for rid, r in raw["roles"].items():
        unknown = [p for p in r.get("permissions", []) if p not in permissions]
        if unknown:
            fail(f"role '{rid}' grants unknown permission(s): {', '.join(unknown)}")
        read_only = bool(r.get("read_only", False))
        if read_only and any(not p.startswith("view_") for p in r.get("permissions", [])):
            fail(f"role '{rid}' is read-only but is granted a permission that changes data")
        sees = None
        if r.get("sees") not in (None, "all"):
            v = r["sees"]
            if not isinstance(v, dict) or set(v) - {"scope", "case_types"}:
                fail(f"role '{rid}': 'sees' takes a scope (all, queues or own) and an optional list of case_types")
            scope = v.get("scope", "all")
            if scope not in SCOPES:
                fail(f"role '{rid}' has unknown visibility scope '{scope}'; use all, queues or own")
            types = v.get("case_types")
            if types is not None and (not isinstance(types, list) or not types or not all(isinstance(x, str) for x in types)):
                fail(f"role '{rid}': case_types must be a non-empty list of case type ids")
            if scope != "all" or types is not None:
                sees = Sees(scope, None if types is None else frozenset(types))
        if RESTRICT in r.get("permissions", []) and RESTRICTED_VIEW not in r.get("permissions", []):
            fail(f"role '{rid}' can restrict cases but could not then see them; grant it '{RESTRICTED_VIEW}' as well")
        customers = _customers(r.get("customers"), f"role '{rid}'", fail)
        facing = r.get("customer_facing", False)
        if not isinstance(facing, bool):
            fail(f"role '{rid}': customer_facing is true or false")
        if facing:
            told = sorted(set(r.get("permissions", [])) & CONFIDENTIAL_PERMISSIONS)
            if told:
                fail(f"role '{rid}' is customer-facing, so it cannot hold {', '.join(told)}: that would tell it about suspicion it must not pass on")
        roles[rid] = Role(rid, r.get("title", rid), frozenset(r.get("permissions", [])), read_only, sees, customers, facing)
        unknown_keys = set(r) - ROLE_KEYS
        if unknown_keys:
            fail(f"role '{rid}' has unknown setting(s): {', '.join(sorted(unknown_keys))}")
    people: dict[str, Persona] = {}
    for item in raw["people"]:
        pid = item.get("id")
        if not pid or pid in people:
            fail(f"person id missing or repeated: {pid!r}")
        names = item.get("roles") or ([item["role"]] if item.get("role") else [])
        if not names or not isinstance(names, list):
            fail(f"person '{pid}' needs a role")
        if str(pid).startswith(PROVIDER_PREFIX):
            fail(f"person '{pid}': ids starting '{PROVIDER_PREFIX}' are kept for the provider's managed-service staff")
        try:
            j, sg = _codes(item.get("jurisdictions"), f"person '{pid}': jurisdictions", fail), _codes(item.get("segments"), f"person '{pid}': segments", fail)
            people[pid] = make_persona(roles, queues, pid, item.get("name", pid), names, item.get("queues", []), item.get("title"),
                                       item.get("about", ""), jurisdictions=j, segments=sg)
        except AccessPolicyError as e:
            fail(str(e))
    default = raw.get("default_person") or next(iter(people))
    if default not in people:
        fail(f"default_person '{default}' is not one of the people")
    ms = _managed_service(raw.get("managed_service"), roles, people, fail)
    tipping = _tipping_off(raw.get("tipping_off"), fail)
    glass = _break_glass(raw.get("break_glass"), fail)
    holders = [rid for rid, r in roles.items() if BREAK_GLASS in r.permissions]
    if holders and glass is None:
        fail(f"role(s) {', '.join(holders)} hold '{BREAK_GLASS}' but the policy has no break_glass section saying for how long")
    for rid in holders:
        if roles[rid].customer_facing:
            fail(f"role '{rid}' is customer-facing and cannot break glass")
    return AccessPolicy(str(raw.get("policy", "default")), str(raw.get("version", "1")), permissions, queues, roles, people, default, ms,
                        tipping, glass)


def make_persona(roles: dict[str, Role], queues: dict[str, str], pid: str, name: str, names: list[str], in_queues: list[str],
                 title: str | None = None, about: str = "", org: str = CLIENT, engagement: str | None = None,
                 jurisdictions: frozenset[str] | None = None, segments: frozenset[str] | None = None) -> Persona:
    """One person holding these roles and queues, with what the roles together allow and show. Raises on a bad combination."""
    missing = [n for n in names if n not in roles]
    if missing:
        raise AccessPolicyError(f"person '{pid}' has unknown role '{missing[0]}'")
    held = [roles[n] for n in names]
    if len(held) > 1 and any(r.read_only for r in held):
        raise AccessPolicyError(f"person '{pid}' combines a read-only role with another role")
    role = held[0]
    granted = frozenset().union(*(r.permissions for r in held))
    bad = [q for q in in_queues if q not in queues]
    if bad:
        raise AccessPolicyError(f"person '{pid}' is in unknown queue(s): {', '.join(bad)}")
    if in_queues and "work" not in granted:
        raise AccessPolicyError(f"person '{pid}' is in a queue but holds no role that can work cases")
    views = None if any(r.sees is None for r in held) else tuple(dict.fromkeys(r.sees for r in held))
    facing = [r.customer_facing for r in held]
    if any(facing) and not all(facing):
        raise AccessPolicyError(f"person '{pid}' combines a customer-facing role with one that is not; tipping-off rules need them kept apart")
    reach = tuple(dict.fromkeys((r.customers or ALL_CUSTOMERS).narrowed(jurisdictions, segments) for r in held))
    customers = None if any(c.everyone for c in reach) else reach
    return Persona(pid, name, role.id, title or role.title, tuple(in_queues), about, granted, role.read_only, tuple(names), views,
                   org, engagement, customers, any(facing))


ROLE_KEYS = {"title", "permissions", "read_only", "sees", "customers", "customer_facing"}
# Permissions that reveal suspicion or law-enforcement interest. A customer-facing role can hold none of them.
CONFIDENTIAL_PERMISSIONS = frozenset({"view_restricted", "restrict_case", "submit_filings", "info_sharing", "sanctions_decisions"})


def _codes(raw, where: str, fail) -> frozenset[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        fail(f"{where} must be a list of codes")
    return frozenset(x.strip() for x in raw)


def _customers(raw, where: str, fail) -> Customers | None:
    if raw is None or raw == "all":
        return None
    if not isinstance(raw, dict) or set(raw) - {"jurisdictions", "segments", "book"}:
        fail(f"{where}: 'customers' takes jurisdictions, segments and book (own or any)")
    book = raw.get("book", "any")
    if book not in ("own", "any"):
        fail(f"{where}: book is 'own' (only customers they manage) or 'any'")
    c = Customers(_codes(raw.get("jurisdictions"), f"{where}: jurisdictions", fail), _codes(raw.get("segments"), f"{where}: segments", fail),
                  book == "own")
    return None if c.everyone else c


def _tipping_off(raw, fail) -> TippingOff:
    if raw is None:
        return TippingOff()
    if not isinstance(raw, dict) or set(raw) - {"case_types", "filings"}:
        fail("tipping_off takes case_types and filings (hidden or visible)")
    types = _codes(raw.get("case_types", []), "tipping_off: case_types", fail)
    filings = raw.get("filings", "hidden")
    if filings not in ("hidden", "visible"):
        fail("tipping_off: filings is 'hidden' or 'visible'")
    return TippingOff(types, filings == "hidden")


def _break_glass(raw, fail) -> BreakGlass | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"hours", "restricted"}:
        fail("break_glass takes hours and restricted")
    hours = raw.get("hours", 4)
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 72:
        fail("break_glass: hours must be a whole number from 1 to 72")
    if not isinstance(raw.get("restricted", False), bool):
        fail("break_glass: restricted is true or false")
    return BreakGlass(hours, raw.get("restricted", False))


def _managed_service(raw, roles: dict[str, Role], people: dict[str, Persona], fail) -> ManagedService | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"provider", "roles", "approval", "max_days", "client_signoff"}:
        fail("managed_service takes: provider, roles, approval, max_days, client_signoff")
    names = raw.get("roles")
    if not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names):
        fail("managed_service: roles must list the roles the provider's staff may be given")
    unknown = [r for r in names if r not in roles]
    if unknown:
        fail(f"managed_service names unknown role(s): {', '.join(unknown)}")
    for r in names:
        kept = sorted(roles[r].permissions & NEVER_DELEGATED)
        if kept:
            fail(f"managed_service: role '{r}' grants {', '.join(kept)}, which stays with the client's own people")
    approval = raw.get("approval", "client")
    if approval not in APPROVALS:
        fail("managed_service: approval must be 'client' (each engagement is approved here) or 'none'")
    days = raw.get("max_days", 180)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 366:
        fail("managed_service: max_days must be a whole number from 1 to 366")
    signoff = raw.get("client_signoff") or []
    if not isinstance(signoff, list) or set(signoff) - set(SIGNOFF_STEPS):
        fail(f"managed_service: client_signoff takes any of: {', '.join(SIGNOFF_STEPS)}")
    if "submit_filings" in signoff and not any(p.can("submit_filings") for p in people.values()):
        fail("managed_service: reports need sending by the client's own people, but none of them can send them")
    provider = str(raw.get("provider") or "Platform provider").strip()[:80]
    return ManagedService(provider, frozenset(names), approval, days, frozenset(signoff))


def load_policy(path: Path) -> AccessPolicy:
    return parse_policy(yaml.safe_load(path.read_text()), path.name)
