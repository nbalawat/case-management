"""AWS: the setup is infra/stacks/aws/bootstrap (state bucket, state key, GitHub OIDC roles, the machine operator role
and instance profile); a machine is infra/stacks/aws/machine. Later commands run as the operator role through an AWS
CLI profile the setup writes, so routine up and down never need administrator rights."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .base import Cloud

OPERATOR_PROFILE = "caseforge-operator"
MARK_BEGIN = "# >>> caseforge cloud machine operator (infra/bin/cloud.py) >>>"
MARK_END = "# <<< caseforge cloud machine operator <<<"

# On-demand Linux prices in us-east-1 (USD), for the estimate printed before every apply. Other regions differ a little.
HOURLY = {"small": 0.2016, "large": 0.4032, "xlarge": 0.8064}
GP3_GB_MONTH = 0.08
PUBLIC_IPV4_HOUR = 0.005


def aws_config_path() -> Path:
    return Path(os.environ.get("AWS_CONFIG_FILE", str(Path.home() / ".aws" / "config"))).expanduser()


def env_keys_present() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID"))


def operator_profile_block(role_arn: str, region: str, source_profile: str) -> str:
    return "\n".join([
        MARK_BEGIN,
        f"[profile {OPERATOR_PROFILE}]",
        f"role_arn = {role_arn}",
        f"source_profile = {source_profile}",
        f"region = {region}",
        "role_session_name = caseforge-cloud-machine",
        "duration_seconds = 14400",
        MARK_END,
        "",
    ])


def write_operator_profile(path: Path, block: str) -> None:
    current = path.read_text() if path.exists() else ""
    if MARK_BEGIN in current:
        start = current.index(MARK_BEGIN)
        end = current.index(MARK_END, start) + len(MARK_END)
        current = (current[:start] + current[end:].lstrip("\n")).rstrip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((current + "\n\n" if current.strip() else "") + block)
    os.chmod(path, 0o600)


class Aws(Cloud):
    name = "aws"
    label = "AWS"
    credentials_hint = (
        "Sign in with the AWS CLI first, as an administrator for `setup` (for example `aws configure` or "
        "`aws sso login --profile <name>` and AWS_PROFILE=<name>). After setup, up/stop/start/down run as the "
        f"{OPERATOR_PROFILE} profile, which assumes the least-privilege operator role."
    )

    def default_region(self) -> str:
        return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    def setup_vars(self, region: str) -> Dict[str, Any]:
        return {"region": region}

    def setup_summary(self, outputs: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "cloud": self.name,
            "region": outputs["region"],
            "state_bucket": outputs["state_bucket"],
            "state_key_arn": outputs["state_key_arn"],
            "operator_role_arn": outputs["machine_operator_role_arn"],
            "instance_profile": outputs["machine_instance_profile"],
        }

    def backend_settings(self, setup: Mapping[str, Any], machine: str) -> Dict[str, Any]:
        return {
            "bucket": setup["state_bucket"],
            "key": f"machine/{machine}.tfstate",
            "region": setup["region"],
            "encrypt": True,
            "kms_key_id": setup["state_key_arn"],
            "use_lockfile": True,
        }

    def machine_vars(self, setup: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "region": setup["region"],
            "state_encryption_key": setup["state_key_arn"],
            "instance_profile_name": setup["instance_profile"],
        }

    def operator_env(self, setup: Mapping[str, Any], as_caller: bool) -> Dict[str, str]:
        if as_caller or env_keys_present() or not setup.get("operator_profile_written"):
            return {}
        return {"AWS_PROFILE": OPERATOR_PROFILE}

    def prepare_operator(self, setup: Mapping[str, Any]) -> Optional[str]:
        if env_keys_present():
            return ("Your AWS credentials are environment variables, which take precedence over profiles, so later "
                    "commands run with them rather than the operator role. Use a named profile (aws configure "
                    f"--profile <name>) to act as {OPERATOR_PROFILE} instead.")
        source = os.environ.get("AWS_PROFILE") or "default"
        if source == OPERATOR_PROFILE:
            return None
        path = aws_config_path()
        write_operator_profile(path, operator_profile_block(setup["operator_role_arn"], setup["region"], source))
        return f"Wrote the [{OPERATOR_PROFILE}] profile to {path} (assumes the operator role from profile '{source}')."

    def hourly_usd(self, size: str) -> float:
        return HOURLY[size]

    def disk_month_usd(self, disk_gb: int) -> float:
        return disk_gb * GP3_GB_MONTH

    def running_extras_hourly_usd(self) -> float:
        return PUBLIC_IPV4_HOUR
