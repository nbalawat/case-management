"""SSH from the laptop: the machine's key, a Host block per machine, and running commands on it.

`ssh <name>` (for example `ssh caseforge`) signs in and forwards every console to the same port on localhost, so the
app is http://localhost:8000 and Keycloak http://localhost:8080 exactly as on a laptop stack. The CLI's own commands
pass ClearAllForwardings so they never compete with an open session for those ports.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from .tofu import ToolError

MARK_BEGIN = "# >>> caseforge cloud machines (infra/bin/cloud.py) >>>"
MARK_END = "# <<< caseforge cloud machines <<<"

Runner = Callable[..., subprocess.CompletedProcess]


def ensure_key(directory: Path, comment: str, runner: Runner = subprocess.run) -> Path:
    """Create an ed25519 key pair in `directory` once; return the private key's path."""
    key = directory / "id_ed25519"
    if key.exists() and key.with_suffix(".pub").exists():
        return key
    if shutil.which("ssh-keygen") is None and runner is subprocess.run:
        raise ToolError("ssh-keygen is missing: install an OpenSSH client")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)], check=True)
    return key


def public_key(private_key: Path) -> str:
    return private_key.with_suffix(".pub").read_text().strip()


def _quote(path: Path) -> str:
    return '"' + str(path).replace('"', '\\"') + '"'


@dataclass(frozen=True)
class HostEntry:
    alias: str
    address: str
    user: str
    key: Path
    known_hosts: Path
    host_key_alias: str
    forwards: Mapping[str, int]

    def render(self) -> str:
        lines = [
            f"Host {self.alias}",
            f"  HostName {self.address}",
            f"  User {self.user}",
            f"  IdentityFile {_quote(self.key)}",
            "  IdentitiesOnly yes",
            # The host key belongs to the machine, not to its address: the address changes on every start.
            f"  HostKeyAlias {self.host_key_alias}",
            f"  UserKnownHostsFile {_quote(self.known_hosts)}",
            "  StrictHostKeyChecking accept-new",
            "  ServerAliveInterval 30",
            "  ServerAliveCountMax 4",
            "  ExitOnForwardFailure no",
        ]
        for name, port in sorted(self.forwards.items(), key=lambda kv: kv[1]):
            lines.append(f"  LocalForward {port} localhost:{port}  # {name}")
        return "\n".join(lines) + "\n"


def render_config(entries: Sequence[HostEntry], stopped: Sequence[str] = ()) -> str:
    out = ["# Written by infra/bin/cloud.py on every up, start, stop and down. Edits here are overwritten.", ""]
    for entry in entries:
        out.append(entry.render())
    for alias in stopped:
        out.append(f"# {alias}: stopped (no address). Start it with: infra/bin/cloud.py start --name {alias}\n")
    return "\n".join(out)


def write_config(path: Path, entries: Sequence[HostEntry], stopped: Sequence[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(render_config(entries, stopped))
    os.chmod(path, 0o600)


def include_block(config: Path) -> str:
    return f"{MARK_BEGIN}\nInclude {_quote(config)}\n{MARK_END}\n"


def ensure_included(user_config: Path, config: Path) -> bool:
    """Put an Include of `config` at the top of ~/.ssh/config (Include only applies globally there). True if changed."""
    block = include_block(config)
    current = user_config.read_text() if user_config.exists() else ""
    if MARK_BEGIN in current:
        start = current.index(MARK_BEGIN)
        end = current.index(MARK_END, start) + len(MARK_END)
        rest = current[:start] + current[end:].lstrip("\n")
        if current[start:end] + "\n" == block:
            return False
        current = rest
    user_config.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(user_config.parent, 0o700)
    if user_config.exists():
        backup = user_config.with_name(user_config.name + ".caseforge-backup")
        if not backup.exists():
            shutil.copy2(user_config, backup)
    user_config.write_text(block + ("\n" + current if current else ""))
    os.chmod(user_config, 0o600)
    return True


@dataclass
class Ssh:
    """Runs commands on a machine through its Host block."""

    config: Path
    alias: str
    binary: str = "ssh"
    runner: Runner = subprocess.run

    def base(self, forwards: bool = False, timeout: int = 10) -> List[str]:
        cmd = [self.binary, "-F", str(self.config), "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes"]
        if not forwards:
            cmd += ["-o", "ClearAllForwardings=yes"]
        return cmd + [self.alias]

    def run(self, command: Sequence[str], capture: bool = False, timeout: int = 10, tty: bool = False,
            stdin: Optional[str] = None) -> subprocess.CompletedProcess:
        cmd = self.base(timeout=timeout)
        if tty:
            cmd.insert(1, "-t")
        kwargs: Dict[str, object] = {"text": True, "capture_output": capture}
        if stdin is not None:
            kwargs["input"] = stdin
        return self.runner(cmd + ["--", *command], **kwargs)

    def reachable(self) -> bool:
        return self.run(["true"], capture=True, timeout=8).returncode == 0

    def interactive(self, command: Sequence[str] = ()) -> List[str]:
        """argv for an interactive session with the consoles forwarded (exec it)."""
        cmd = [self.binary, "-F", str(self.config), self.alias]
        return cmd + (["--", *command] if command else [])
