"""Stand-ins for OpenTofu, SSH and the network, so the lifecycle runs end to end with no cloud and no tools."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caseforge_cloud.lifecycle import Lifecycle  # noqa: E402
from caseforge_cloud.clouds.aws import Aws  # noqa: E402
from caseforge_cloud.home import Home  # noqa: E402

SETUP_OUTPUTS = {
    "region": "us-east-1",
    "state_bucket": "caseforge-tofu-state-123456789012",
    "state_key_arn": "arn:aws:kms:us-east-1:123456789012:key/k",
    "machine_operator_role_arn": "arn:aws:iam::123456789012:role/caseforge-machine-operator",
    "machine_instance_profile": "caseforge-machine",
    "github_role_arns": {},
}


@dataclass
class FakeTofu:
    root: Path
    data_dir: Path
    env: Dict[str, str]
    world: "World"

    def check_version(self) -> str:
        return "1.11.5"

    def init(self, backend_config: Optional[Path] = None, var_file: Optional[Path] = None) -> None:
        self.world.calls.append(("init", self.root.name, backend_config.read_text() if backend_config else None))

    def plan(self, out: Path, var_file: Optional[Path] = None, destroy: bool = False, state: Optional[Path] = None,
             extra=()) -> bool:
        values = json.loads(var_file.read_text()) if var_file else {}
        self.world.calls.append(("plan", self.root.name, destroy, values, dict(self.env)))
        self.world.pending = (self.root.name, destroy, values)
        out.write_text("plan")
        if self.root.name == "machine":
            if destroy:
                return self.world.machine is not None
            return self.world.machine is None or self.world.machine["running"] != values["running"] or self.world.cidrs != values["allowed_cidrs"]
        return not self.world.setup_applied

    def apply(self, plan: Path, state: Optional[Path] = None) -> None:
        root, destroy, values = self.world.pending
        self.world.calls.append(("apply", root, destroy))
        if root != "machine":
            self.world.setup_applied = True
            return
        if destroy:
            self.world.machine = None
            return
        self.world.cidrs = values["allowed_cidrs"]
        address = self.world.next_ip if values["running"] else ""
        self.world.machine = {"cloud": "aws", "region": "us-east-1", "id": self.world.instance_id,
                              "machine_type": "m7i.2xlarge", "public_ip": address, "running": values["running"],
                              "console_url": "https://console.example/i"}

    def outputs(self, state: Optional[Path] = None) -> Dict[str, Any]:
        if self.root.name != "machine":
            return dict(SETUP_OUTPUTS)
        return {"machine": self.world.machine, "ssh_user": "caseforge", "forwarded_ports": {"app": 8000, "keycloak": 8080}}


@dataclass
class FakeSsh:
    config: Path
    alias: str
    world: "World"

    def run(self, command, capture=False, timeout=10, tty=False, stdin=None):
        self.world.remote.append((self.alias, list(command), stdin))
        return subprocess.CompletedProcess(command, self.world.remote_rc, "", "")

    def reachable(self) -> bool:
        return self.world.reachable

    def interactive(self, command=()):
        return ["ssh", "-F", str(self.config), self.alias, *command]


@dataclass
class World:
    tmp: Path
    calls: List[Any] = field(default_factory=list)
    remote: List[Any] = field(default_factory=list)
    output: List[str] = field(default_factory=list)
    answers: List[str] = field(default_factory=list)
    machine: Optional[Dict[str, Any]] = None
    cidrs: Optional[List[str]] = None
    setup_applied: bool = False
    pending: Any = None
    next_ip: str = "203.0.113.10"
    my_ip: Optional[str] = "198.51.100.7"
    instance_id: str = "i-0abc"
    reachable: bool = True
    remote_rc: int = 0

    def keygen(self, directory: Path, comment: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        key = directory / "id_ed25519"
        if not key.exists():
            key.write_text("PRIVATE")
            key.with_suffix(".pub").write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake {comment}\n")
        return key

    def lifecycle(self, assume_yes: bool = True, as_caller: bool = False) -> Lifecycle:
        return Lifecycle(
            cloud=Aws(), home=Home(self.tmp / "home"), out=self.output.append,
            prompt=lambda q: self.answers.pop(0) if self.answers else "", assume_yes=assume_yes, as_caller=as_caller,
            tofu_factory=lambda root, data, env: FakeTofu(root, data, env, self),
            ssh_factory=lambda config, alias: FakeSsh(config, alias, self),
            detect_ip=lambda: self.my_ip, keygen=self.keygen, user_ssh_config=self.tmp / "dot-ssh" / "config",
            sleep=lambda s: None)
