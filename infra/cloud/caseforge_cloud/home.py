"""Where the laptop keeps what the cloud machine needs between commands.

    ~/.caseforge/cloud/                       (CASEFORGE_CLOUD_HOME overrides)
      ssh/config                              Host blocks for every machine; ~/.ssh/config includes this file
      <cloud>/setup/terraform.tfstate         the one-time setup's state (no secrets)
      <cloud>/setup/outputs.json              its outputs: state bucket, key, operator role, region
      <cloud>/machines/<name>/id_ed25519      the machine's SSH key; the private half never leaves this folder
      <cloud>/machines/<name>/machine.json    the last known machine output and options

Nothing here is needed to tear a machine down except the setup outputs: the machine's state lives in the cloud's
state bucket, so a machine brought up from one laptop can be stopped or deleted from another.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def default_root() -> Path:
    override = os.environ.get("CASEFORGE_CLOUD_HOME")
    return Path(override).expanduser() if override else Path.home() / ".caseforge" / "cloud"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def write_json(path: Path, data: Dict[str, Any]) -> None:
    _write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


@dataclass(frozen=True)
class Home:
    root: Path

    @classmethod
    def default(cls) -> "Home":
        return cls(default_root())

    @property
    def ssh_config(self) -> Path:
        return self.root / "ssh" / "config"

    def setup_dir(self, cloud: str) -> Path:
        return self.root / cloud / "setup"

    def machines_dir(self, cloud: str) -> Path:
        return self.root / cloud / "machines"

    def machine_dir(self, cloud: str, name: str) -> Path:
        return self.machines_dir(cloud) / name

    def setup_outputs(self, cloud: str) -> Optional[Dict[str, Any]]:
        return read_json(self.setup_dir(cloud) / "outputs.json")

    def save_setup_outputs(self, cloud: str, outputs: Dict[str, Any]) -> None:
        write_json(self.setup_dir(cloud) / "outputs.json", outputs)

    def machine_record(self, cloud: str, name: str) -> Optional[Dict[str, Any]]:
        return read_json(self.machine_dir(cloud, name) / "machine.json")

    def save_machine_record(self, cloud: str, name: str, record: Dict[str, Any]) -> None:
        write_json(self.machine_dir(cloud, name) / "machine.json", record)

    def all_machine_records(self) -> Dict[str, Dict[str, Any]]:
        """Every machine this laptop knows, keyed by name (names are unique across clouds by construction)."""
        found: Dict[str, Dict[str, Any]] = {}
        if not self.root.exists():
            return found
        for record in sorted(self.root.glob("*/machines/*/machine.json")):
            data = read_json(record)
            if data:
                found[record.parent.name] = data
        return found
