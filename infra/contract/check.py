#!/usr/bin/env python3
"""Fail when the cloud stamp modules stop sharing one contract.

1. modules/<cloud>/stamp/contract.tf is byte-identical to contract/stamp-variables.tf for every cloud.
2. Every cloud's `contract` output declares the same keys, at the top level and inside each nested object.
3. The cloud machine: modules/<cloud>/machine/contract.tf is byte-identical to contract/machine-variables.tf, and its
   `machine` output has exactly the keys listed in contract/machine-outputs.md, for every cloud that has one.

Run with --fix to copy the contract variables into each module.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent
SOURCE = INFRA / "contract" / "stamp-variables.tf"
CLOUDS = ("aws", "azure", "gcp")


MACHINE_SOURCE = INFRA / "contract" / "machine-variables.tf"
MACHINE_OUTPUTS = INFRA / "contract" / "machine-outputs.md"


def output_keys(text: str, output: str = "contract") -> dict[str, set[str]]:
    """Keys of an output's object, by nesting path, read from the HCL text by brace depth."""
    body = text[text.index(f'output "{output}"'):]
    body = body[body.index("value = {") + len("value = {"):]
    keys: dict[str, set[str]] = {"": set()}
    stack: list[str] = [""]
    depth_names: list[str] = []
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m and len(stack) == len(depth_names) + 1:
            name, rest = m.groups()
            path = stack[-1]
            keys.setdefault(path, set()).add(name)
            opens = rest.count("{") - rest.count("}")
            if rest.startswith("{") and opens > 0:
                depth_names.append(name)
                stack.append(f"{stack[-1]}/{name}".strip("/"))
            continue
        closes = line.count("}") - line.count("{")
        for _ in range(max(closes, 0)):
            if depth_names:
                depth_names.pop()
                stack.pop()
            else:
                return keys
    return keys


def main() -> int:
    fix = "--fix" in sys.argv
    problems: list[str] = []
    source = SOURCE.read_bytes()
    for cloud in CLOUDS:
        target = INFRA / "modules" / cloud / "stamp" / "contract.tf"
        if fix:
            shutil.copyfile(SOURCE, target)
        elif target.read_bytes() != source:
            problems.append(f"{target.relative_to(INFRA)} differs from contract/stamp-variables.tf (run `make infra-contract`)")

    shapes = {c: output_keys((INFRA / "modules" / c / "stamp" / "outputs.tf").read_text()) for c in CLOUDS}
    reference = shapes["aws"]
    # Only the shared nesting levels are compared; secret_store and extra_env differ by design.
    for cloud, shape in shapes.items():
        for path in ("", "storage", "database", "event_bus", "search"):
            if shape.get(path) != reference.get(path):
                problems.append(f"{cloud}: contract output keys at '{path or 'top'}' are {sorted(shape.get(path, []))}, expected {sorted(reference.get(path, []))}")

    problems += check_machine(fix)

    for p in problems:
        print(f"contract: {p}", file=sys.stderr)
    if not problems:
        print(f"contract: {len(CLOUDS)} stamp modules share one contract ({len(reference[''])} output keys)")
    return 1 if problems else 0


def documented_machine_keys() -> set[str]:
    """The keys in the table of contract/machine-outputs.md: rows that start with | `key` |."""
    return set(re.findall(r"^\| `([a-z_]+)` \|", MACHINE_OUTPUTS.read_text(), flags=re.M))


def check_machine(fix: bool) -> list[str]:
    problems: list[str] = []
    source = MACHINE_SOURCE.read_bytes()
    expected = documented_machine_keys()
    clouds = [c for c in CLOUDS if (INFRA / "modules" / c / "machine").is_dir()]
    if not clouds:
        return ["no cloud has a machine module"]
    for cloud in clouds:
        module = INFRA / "modules" / cloud / "machine"
        target = module / "contract.tf"
        if fix:
            shutil.copyfile(MACHINE_SOURCE, target)
        elif not target.exists() or target.read_bytes() != source:
            problems.append(f"{target.relative_to(INFRA)} differs from contract/machine-variables.tf (run `make infra-contract`)")
        keys = output_keys((module / "outputs.tf").read_text(), "machine").get("", set())
        if keys != expected:
            problems.append(f"{cloud}: machine output keys are {sorted(keys)}, contract/machine-outputs.md lists {sorted(expected)}")
    if not problems:
        print(f"contract: machine modules for {', '.join(clouds)} match the machine contract ({len(expected)} output keys)")
    return problems


if __name__ == "__main__":
    sys.exit(main())
