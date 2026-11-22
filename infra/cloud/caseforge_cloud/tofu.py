"""A thin, testable wrapper around the OpenTofu CLI: one object per root module and data directory."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

MIN_VERSION = (1, 11, 0)

Runner = Callable[..., subprocess.CompletedProcess]


class ToolError(RuntimeError):
    """A required tool is missing or too old, or a command failed."""


def parse_version(text: str) -> Optional[tuple]:
    m = re.search(r"OpenTofu v(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


@dataclass
class Tofu:
    """Runs `tofu` in one root module, with its own data directory and environment."""

    root: Path
    data_dir: Path
    binary: str = field(default_factory=lambda: os.environ.get("TOFU", "tofu"))
    env: Dict[str, str] = field(default_factory=dict)
    runner: Runner = subprocess.run

    def _environ(self) -> Dict[str, str]:
        environ = dict(os.environ)
        environ.update(self.env)
        environ["TF_DATA_DIR"] = str(self.data_dir)
        environ.setdefault("TF_IN_AUTOMATION", "1")
        return environ

    def run(self, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, f"-chdir={self.root}", *args]
        result = self.runner(cmd, env=self._environ(), text=True, capture_output=capture)
        if check and result.returncode != 0:
            detail = (result.stderr or "").strip() if capture else ""
            raise ToolError(f"`tofu {args[0]}` failed in {self.root.name}" + (f":\n{detail}" if detail else ""))
        return result

    def check_version(self) -> str:
        if shutil.which(self.binary) is None and self.runner is subprocess.run:
            raise ToolError("OpenTofu is not installed. On a Mac: brew install opentofu  (others: https://opentofu.org/docs/intro/install/)")
        out = self.run("version", capture=True).stdout
        version = parse_version(out)
        if version is None or version < MIN_VERSION:
            need = ".".join(map(str, MIN_VERSION))
            raise ToolError(f"OpenTofu {need} or later is needed (found: {out.strip().splitlines()[0] if out else 'unknown'}).")
        return ".".join(map(str, version))

    def init(self, backend_config: Optional[Path] = None, var_file: Optional[Path] = None) -> None:
        args: List[str] = ["init", "-input=false", "-reconfigure"]
        if backend_config is not None:
            args.append(f"-backend-config={backend_config}")
        if var_file is not None:
            args.append(f"-var-file={var_file}")
        self.run(*args)

    def plan(self, out: Path, var_file: Optional[Path] = None, destroy: bool = False, state: Optional[Path] = None,
             extra: Sequence[str] = ()) -> bool:
        """Write a plan file; True when it changes something."""
        args: List[str] = ["plan", "-input=false", "-detailed-exitcode", f"-out={out}"]
        if var_file is not None:
            args.append(f"-var-file={var_file}")
        if destroy:
            args.append("-destroy")
        if state is not None:
            args.append(f"-state={state}")
        args.extend(extra)
        result = self.run(*args, check=False)
        if result.returncode == 1:
            raise ToolError(f"`tofu plan` failed in {self.root.name}")
        return result.returncode == 2

    def apply(self, plan: Path, state: Optional[Path] = None, var_file: Optional[Path] = None) -> None:
        """Apply a saved plan. The variables go in again: state encryption needs them to read the plan at all."""
        args: List[str] = ["apply", "-input=false"]
        if state is not None:
            args.append(f"-state={state}")
        if var_file is not None:
            args.append(f"-var-file={var_file}")
        args.append(str(plan))
        self.run(*args)

    def outputs(self, state: Optional[Path] = None, var_file: Optional[Path] = None) -> Dict[str, Any]:
        args: List[str] = ["output", "-json"]
        if state is not None:
            args.append(f"-state={state}")
        if var_file is not None:  # encrypted state cannot be read without the key's variables
            args.append(f"-var-file={var_file}")
        raw = json.loads(self.run(*args, capture=True).stdout or "{}")
        return {k: v.get("value") for k, v in raw.items()}


def write_backend_config(path: Path, settings: Mapping[str, Any]) -> Path:
    """Write an HCL backend file from flat settings (strings, booleans and numbers)."""
    lines = []
    for key in sorted(settings):
        value = settings[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = json.dumps(str(value))
        lines.append(f"{key} = {rendered}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
