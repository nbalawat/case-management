#!/usr/bin/env python3
"""Read or set the image tag released to every stamp of an environment (deploy/environments/<env>/*.yaml).

    deploy/scripts/set-release.py <env> <version>   set it on every stamp
    deploy/scripts/set-release.py <env> --show      print it (all stamps must agree)

Only the `tag:` line is rewritten, so comments and anything else in the files stay as they are.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENVIRONMENTS = Path(__file__).resolve().parent.parent / "environments"
VERSION = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
TAG = re.compile(r'^(\s+tag:\s*)"?([^"\s#]*)"?(.*)$', re.M)


def stamps(env: str) -> list[Path]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", env):
        raise SystemExit(f"bad environment name {env!r}")
    files = sorted((ENVIRONMENTS / env).glob("*.yaml"))
    if not files:
        raise SystemExit(f"no stamps in {ENVIRONMENTS / env}")
    return files


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    env, arg = argv
    files = stamps(env)
    if arg == "--show":
        tags = {m.group(2) for f in files for m in [TAG.search(f.read_text())] if m}
        if len(tags) != 1:
            raise SystemExit(f"{env}: stamps disagree on the tag: {sorted(tags)}")
        print(tags.pop())
        return 0
    if not VERSION.match(arg):
        raise SystemExit(f"{arg!r} is not a version like 1.2.3")
    for f in files:
        text = f.read_text()
        new, n = TAG.subn(lambda m: f'{m.group(1)}"{arg}"{m.group(3)}', text, count=1)
        if n != 1:
            raise SystemExit(f"{f}: no image tag line")
        f.write_text(new)
        print(f"{f.relative_to(ENVIRONMENTS)}: {arg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
