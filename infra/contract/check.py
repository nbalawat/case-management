#!/usr/bin/env python3
"""Fail when the cloud stamp modules stop sharing one contract.

1. modules/<cloud>/stamp/contract.tf is byte-identical to contract/stamp-variables.tf for every cloud.
2. Every cloud's `contract` output declares the same keys, at the top level and inside each nested object.

Run with --fix to copy contract/stamp-variables.tf into each module.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent
SOURCE = INFRA / "contract" / "stamp-variables.tf"
CLOUDS = ("aws", "azure", "gcp")


def output_keys(text: str) -> dict[str, set[str]]:
    """Keys of the `contract` output's object, by nesting path, read from the HCL text by brace depth."""
    body = text[text.index('output "contract"'):]
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

    for p in problems:
        print(f"contract: {p}", file=sys.stderr)
    if not problems:
        print(f"contract: {len(CLOUDS)} stamp modules share one contract ({len(reference[''])} output keys)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
