"""What the lifecycle needs from a cloud. Everything else (SSH, cloud-init, the stack) is cloud-neutral."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

INFRA = Path(__file__).resolve().parents[3]


class NotSupported(RuntimeError):
    """The cloud is not implemented yet, or a setting is not available on it."""


class Cloud(ABC):
    name: str = ""
    label: str = ""

    @property
    def setup_root(self) -> Path:
        """Root module of the one-time setup (state store, state key, operator identity), run with local state."""
        return INFRA / "stacks" / self.name / "bootstrap"

    @property
    def machine_root(self) -> Path:
        """Root module of one machine."""
        return INFRA / "stacks" / self.name / "machine"

    @abstractmethod
    def default_region(self) -> str: ...

    @abstractmethod
    def setup_vars(self, region: str) -> Dict[str, Any]:
        """Variables for the setup root."""

    @abstractmethod
    def setup_summary(self, outputs: Mapping[str, Any]) -> Dict[str, Any]:
        """The setup outputs the machine needs, as a flat record kept on the laptop."""

    @abstractmethod
    def backend_settings(self, setup: Mapping[str, Any], machine: str) -> Dict[str, Any]:
        """Backend settings for the machine root's state, in the setup's state store."""

    @abstractmethod
    def machine_vars(self, setup: Mapping[str, Any]) -> Dict[str, Any]:
        """Cloud-specific variables for the machine root (region, state key, instance identity)."""

    def operator_env(self, setup: Mapping[str, Any], as_caller: bool) -> Dict[str, str]:
        """Environment for OpenTofu when it manages the machine (for example the operator profile)."""
        return {}

    def prepare_operator(self, setup: Mapping[str, Any]) -> Optional[str]:
        """One-time laptop configuration so later commands act as the least-privilege operator. Returns a note."""
        return None

    @abstractmethod
    def hourly_usd(self, size: str) -> float:
        """On-demand compute price per hour for `size`, an estimate."""

    @abstractmethod
    def disk_month_usd(self, disk_gb: int) -> float:
        """Price of the disk per month, an estimate."""

    def running_extras_hourly_usd(self) -> float:
        """Other per-hour charges while running (for example a public address)."""
        return 0.0

    credentials_hint: str = ""


class PendingCloud(Cloud):
    """A cloud whose machine module is not written yet: every command explains what is missing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.label = name

    def missing(self) -> NotSupported:
        return NotSupported(
            f"{self.name} is not implemented yet. It needs infra/modules/{self.name}/machine (the machine contract in "
            f"infra/contract/machine-variables.tf), infra/stacks/{self.name}/machine and a Cloud class in "
            f"infra/cloud/caseforge_cloud/clouds/{self.name}.py; the bootstrap, SSH and lifecycle code is shared."
        )

    def default_region(self) -> str:
        raise self.missing()

    def setup_vars(self, region: str) -> Dict[str, Any]:
        raise self.missing()

    def setup_summary(self, outputs: Mapping[str, Any]) -> Dict[str, Any]:
        raise self.missing()

    def backend_settings(self, setup: Mapping[str, Any], machine: str) -> Dict[str, Any]:
        raise self.missing()

    def machine_vars(self, setup: Mapping[str, Any]) -> Dict[str, Any]:
        raise self.missing()

    def hourly_usd(self, size: str) -> float:
        raise self.missing()

    def disk_month_usd(self, disk_gb: int) -> float:
        raise self.missing()
