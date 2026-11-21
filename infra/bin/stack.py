#!/usr/bin/env python3
"""Run OpenTofu for one layer of one environment, the same way on a laptop and in CI.

    infra/bin/stack.py <cloud> <env> <layer> [--stamp NAME] <command> [-- extra tofu args]

    cloud    aws | azure | gcp
    env      a directory under infra/envs/<cloud>/ (dev, staging, prod)
    layer    bootstrap | platform | addons | stamp
    command  init | plan | apply | destroy | output | validate | show

It picks the root module (infra/stacks/<cloud>/<layer>), the backend settings (envs/<cloud>/<env>/backend.hcl) with
a state key per layer and stamp, the variable files (common.tfvars, then <layer>.tfvars or stamps/<stamp>.tfvars),
and tells the add-ons and stamp layers where the platform's state is. Each combination gets its own data directory,
so switching environments never reuses another's providers or backend. `plan` writes an encrypted plan file and
`apply` applies exactly that file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent
CLOUDS = ("aws", "azure", "gcp")
LAYERS = ("bootstrap", "platform", "addons", "stamp")
COMMANDS = ("init", "plan", "apply", "destroy", "output", "validate", "show")
BACKEND_TYPE = {"aws": "s3", "azure": "azurerm", "gcp": "gcs"}
NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


@dataclass(frozen=True)
class Stack:
    cloud: str
    env: str
    layer: str
    stamp: str | None

    @property
    def root(self) -> Path:
        return INFRA / "stacks" / self.cloud / self.layer

    @property
    def env_dir(self) -> Path:
        return INFRA / "envs" / self.cloud / self.env

    @property
    def state_name(self) -> str:
        return f"stamps/{self.stamp}" if self.layer == "stamp" else self.layer

    @property
    def data_dir(self) -> Path:
        suffix = f"-{self.stamp}" if self.stamp else ""
        return INFRA / ".tofu" / f"{self.cloud}-{self.env}-{self.layer}{suffix}"

    @property
    def plan_file(self) -> Path:
        return self.data_dir / "tfplan"


def parse_backend(path: Path) -> dict[str, str]:
    """backend.hcl holds simple `key = "value"` lines; that is all this reads."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"]*)"?$', line)
        if not m:
            raise SystemExit(f"{path}: cannot read line {line!r}; use key = \"value\"")
        values[m.group(1)] = m.group(2)
    return values


def state_settings(stack: Stack, backend: dict[str, str], layer: str | None = None) -> dict[str, str]:
    name = layer or stack.state_name
    settings = dict(backend)
    if stack.cloud == "gcp":
        settings["prefix"] = f"{stack.env}/{name}"
    else:
        settings["key"] = f"{stack.env}/{name}.tfstate"
    if stack.cloud == "aws":
        settings.setdefault("use_lockfile", "true")
        settings.setdefault("encrypt", "true")
    if stack.cloud == "azure":
        settings.setdefault("use_azuread_auth", "true")
    return settings


def var_files(stack: Stack) -> list[Path]:
    files = [stack.env_dir / "common.tfvars"]
    files.append(stack.env_dir / "stamps" / f"{stack.stamp}.tfvars" if stack.layer == "stamp" else stack.env_dir / f"{stack.layer}.tfvars")
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit("missing variable files: " + ", ".join(str(m.relative_to(INFRA)) for m in missing))
    return files


def run(cmd: list[str], env: dict[str, str]) -> int:
    print("+ " + " ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd, env=env)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cloud", choices=CLOUDS)
    parser.add_argument("env")
    parser.add_argument("layer", choices=LAYERS)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--stamp", help="stamp name (layer stamp only)")
    parser.add_argument("--tofu", default=os.environ.get("TOFU", "tofu"), help="OpenTofu binary")
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="after --, passed to tofu")
    args = parser.parse_args(argv)

    if not NAME.match(args.env):
        parser.error("env must be lowercase letters, digits and hyphens")
    if (args.layer == "stamp") != bool(args.stamp):
        parser.error("--stamp is required for the stamp layer and only for it")
    if args.stamp and not NAME.match(args.stamp):
        parser.error("stamp must be lowercase letters, digits and hyphens")

    stack = Stack(args.cloud, args.env, args.layer, args.stamp)
    extra = [a for a in args.extra if a != "--"]
    stack.data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TF_DATA_DIR=str(stack.data_dir), TF_IN_AUTOMATION=os.environ.get("CI", "") and "1")
    chdir = f"-chdir={stack.root}"

    if args.layer == "bootstrap":
        # Bootstrap creates the state backend, so it keeps local state (move it into the backend afterwards).
        if args.command == "init":
            return run([args.tofu, chdir, "init", "-input=false", *extra], env)
        files = [stack.env_dir / "bootstrap.tfvars"]
        extra_vars = []
    else:
        backend = parse_backend(stack.env_dir / "backend.hcl")
        if args.command == "init":
            cmd = [args.tofu, chdir, "init", "-input=false", "-reconfigure"]
            cmd += [f"-backend-config={k}={v}" for k, v in state_settings(stack, backend).items()]
            return run(cmd + extra, env)
        files = var_files(stack)
        extra_vars = []
        if args.layer in ("addons", "stamp"):
            platform_state = state_settings(stack, backend, layer="platform")
            if args.cloud == "aws":
                platform_state.pop("use_lockfile", None)
            tmp = tempfile.NamedTemporaryFile("w", suffix=".tfvars.json", dir=stack.data_dir, delete=False)
            json.dump({"platform_state": platform_state}, tmp)
            tmp.close()
            extra_vars = [Path(tmp.name)]

    var_args = [f"-var-file={f}" for f in [*files, *extra_vars]]
    if args.command == "plan":
        cmd = [args.tofu, chdir, "plan", "-input=false", f"-out={stack.plan_file}", *var_args]
    elif args.command == "apply":
        if not stack.plan_file.exists():
            raise SystemExit("no plan: run `plan` first, review it, then `apply` applies exactly that plan")
        cmd = [args.tofu, chdir, "apply", "-input=false", str(stack.plan_file)]
    elif args.command == "destroy":
        cmd = [args.tofu, chdir, "destroy", "-input=false", *var_args]
    elif args.command == "validate":
        cmd = [args.tofu, chdir, "validate"]
    elif args.command == "show":
        cmd = [args.tofu, chdir, "show", str(stack.plan_file)]
    else:
        cmd = [args.tofu, chdir, "output"]
    return run(cmd + extra, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
