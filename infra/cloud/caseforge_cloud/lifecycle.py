"""setup, up, stop, start, down and status for one cloud machine, with every outside effect injected."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import costs, netaddr
from . import ssh as sshmod
from .clouds.base import Cloud
from .home import Home, write_json
from .tofu import Tofu, write_backend_config

NAME = re.compile(r"^caseforge(-[a-z0-9]{1,20})?$")
SIZES = ("small", "large", "xlarge")
STACKS = ("demo", "sso", "backbone", "backbone-cdc", "full", "observability", "lineage", "streaming")
# Placeholders for destroying a machine whose options this laptop never saw: destroy ignores them.
DESTROY_PLACEHOLDERS = {"allowed_cidrs": ["192.0.2.1/32"], "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderForDestroyOnly caseforge"}


class UserError(RuntimeError):
    """Something the operator can fix; printed without a traceback."""


@dataclass
class Options:
    name: str = "caseforge"
    size: str = "large"
    disk_gb: int = 150
    stack: str = "full"
    git_ref: str = "main"
    auto_stop_hours: int = 10
    allowed_cidrs: List[str] = field(default_factory=list)
    budget_email: str = ""
    budget_usd: float = 150

    def validate(self) -> None:
        if not NAME.match(self.name):
            raise UserError(f"machine names are 'caseforge' or 'caseforge-<suffix>' (lower case): got '{self.name}'")
        if self.size not in SIZES:
            raise UserError(f"size must be one of {', '.join(SIZES)}")
        if self.stack not in STACKS:
            raise UserError(f"stack must be one of {', '.join(STACKS)}")
        if not 60 <= self.disk_gb <= 2000:
            raise UserError("disk must be between 60 and 2000 GB")
        if not 0 <= self.auto_stop_hours <= 168:
            raise UserError("auto-stop hours must be between 0 and 168")


Prompt = Callable[[str], str]
Out = Callable[[str], None]
TofuFactory = Callable[[Path, Path, Dict[str, str]], Tofu]
SshFactory = Callable[[Path, str], sshmod.Ssh]


@dataclass
class Lifecycle:
    cloud: Cloud
    home: Home
    out: Out = print
    prompt: Prompt = input
    assume_yes: bool = False
    as_caller: bool = False
    tofu_factory: TofuFactory = lambda root, data, env: Tofu(root=root, data_dir=data, env=env)
    ssh_factory: SshFactory = lambda config, alias: sshmod.Ssh(config=config, alias=alias)
    detect_ip: Callable[[], Optional[str]] = netaddr.public_ipv4
    keygen: Callable[[Path, str], Path] = sshmod.ensure_key
    user_ssh_config: Path = field(default_factory=lambda: Path.home() / ".ssh" / "config")
    sleep: Callable[[float], None] = time.sleep

    # ---- helpers
    def confirm(self, question: str, expect: str = "yes") -> None:
        if self.assume_yes:
            return
        try:
            answer = self.prompt(f"{question} Type '{expect}' to go ahead: ").strip()
        except EOFError:
            answer = ""
        if answer != expect:
            raise UserError("stopped: nothing was changed")

    def _setup(self) -> Dict[str, Any]:
        setup = self.home.setup_outputs(self.cloud.name)
        if not setup:
            raise UserError(f"run setup first: infra/bin/cloud.py setup --cloud {self.cloud.name}")
        return setup

    def _machine_tofu(self, setup: Mapping[str, Any], name: str) -> Tofu:
        mdir = self.home.machine_dir(self.cloud.name, name)
        return self.tofu_factory(self.cloud.machine_root, mdir / "tofu", self.cloud.operator_env(setup, self.as_caller))

    def _vars(self, setup: Mapping[str, Any], opts: Options, running: bool, public_key: str) -> Dict[str, Any]:
        values: Dict[str, Any] = dict(self.cloud.machine_vars(setup))
        values.update({
            "name": opts.name, "size": opts.size, "disk_gb": opts.disk_gb, "stack": opts.stack,
            "git_ref": opts.git_ref, "auto_stop_hours": opts.auto_stop_hours, "allowed_cidrs": opts.allowed_cidrs,
            "budget_email": opts.budget_email, "budget_usd": opts.budget_usd, "running": running,
            "ssh_public_key": public_key,
        })
        return values

    def _init(self, tofu: Tofu, setup: Mapping[str, Any], name: str, var_file: Path) -> None:
        backend = write_backend_config(self.home.machine_dir(self.cloud.name, name) / "backend.hcl",
                                       self.cloud.backend_settings(setup, name))
        tofu.init(backend_config=backend, var_file=var_file)

    def _plan_and_apply(self, tofu: Tofu, plan: Path, var_file: Path, question: str, destroy: bool = False,
                        expect: str = "yes") -> bool:
        changed = tofu.plan(out=plan, var_file=var_file, destroy=destroy)
        if not changed:
            self.out("No changes needed.")
            plan.unlink(missing_ok=True)
            return False
        self.confirm(question, expect)
        tofu.apply(plan, var_file=var_file)
        plan.unlink(missing_ok=True)
        return True

    def _resolve_cidrs(self, requested: Sequence[str]) -> List[str]:
        if requested:
            try:
                return sorted({netaddr.normalise_cidr(c) for c in requested})
            except ValueError as e:
                raise UserError(str(e)) from e
        ip = self.detect_ip()
        if not ip:
            raise UserError("could not find this computer's public address; pass --allow <your-ip>/32")
        return [f"{ip}/32"]

    def _record(self, name: str, opts: Options, machine: Mapping[str, Any], ssh_user: str,
                forwards: Mapping[str, int]) -> Dict[str, Any]:
        record = {"cloud": self.cloud.name, "options": asdict(opts), "machine": dict(machine), "ssh_user": ssh_user,
                  "forwarded_ports": dict(forwards), "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self.home.save_machine_record(self.cloud.name, name, record)
        return record

    def refresh_ssh_config(self) -> None:
        entries: List[sshmod.HostEntry] = []
        stopped: List[str] = []
        for name, record in self.home.all_machine_records().items():
            machine = record.get("machine", {})
            mdir = self.home.machine_dir(record.get("cloud", self.cloud.name), name)
            if machine.get("running") and machine.get("public_ip"):
                entries.append(sshmod.HostEntry(
                    alias=name, address=machine["public_ip"], user=record.get("ssh_user", "caseforge"),
                    key=mdir / "id_ed25519", known_hosts=mdir / "known_hosts",
                    host_key_alias=f"{name}-{machine.get('id', 'machine')}", forwards=record.get("forwarded_ports", {})))
            else:
                stopped.append(name)
        sshmod.write_config(self.home.ssh_config, entries, stopped)

    def _ssh(self, name: str) -> sshmod.Ssh:
        return self.ssh_factory(self.home.ssh_config, name)

    def wait_until_ready(self, name: str, minutes: int = 45) -> bool:
        ssh = self._ssh(name)
        self.out("Waiting for SSH (the machine is booting)...")
        deadline = time.monotonic() + 10 * 60
        while not ssh.reachable():
            if time.monotonic() > deadline:
                self.out("SSH did not answer within 10 minutes. Check the console link above, or run status later.")
                return False
            self.sleep(10)
        self.out("Signed in. Waiting for the first-boot setup and the stack (the first start builds images and seeds "
                 "the demo, about 10 to 20 minutes)...")
        ssh.run(["cloud-init", "status", "--wait"], capture=True, timeout=15)
        result = ssh.run(["caseforge", "wait", str(minutes)], timeout=15)
        return result.returncode == 0

    def include_ssh_config(self) -> None:
        """Make `ssh <name>` work; if ~/.ssh/config cannot be written, say how to fix it and carry on."""
        try:
            changed = sshmod.ensure_included(self.user_ssh_config, self.home.ssh_config)
        except OSError as e:
            self.out(f"Could not edit {self.user_ssh_config} ({e.strerror}). Everything else is set up. Fix the "
                     "file's owner and run setup again (it creates nothing new):\n"
                     "  sudo chown $(whoami) ~/.ssh ~/.ssh/config && chmod 700 ~/.ssh && chmod 600 ~/.ssh/config\n"
                     f"Or add this line at the top of {self.user_ssh_config} yourself:\n"
                     f"  Include \"{self.home.ssh_config}\"")
            return
        if changed:
            self.out(f"Added an Include of {self.home.ssh_config} at the top of {self.user_ssh_config} "
                     "(a backup of the original is next to it).")

    # ---- commands
    def setup(self, region: Optional[str]) -> Dict[str, Any]:
        region = region or self.cloud.default_region()
        sdir = self.home.setup_dir(self.cloud.name)
        tofu = self.tofu_factory(self.cloud.setup_root, sdir / "tofu", {})
        tofu.check_version()
        var_file = sdir / "setup.tfvars.json"
        write_json(var_file, self.cloud.setup_vars(region))
        state = sdir / "terraform.tfstate"
        self.out(f"Setting up {self.cloud.label} in {region}: encrypted state store, state key, least-privilege "
                 "operator role and the machine's instance profile. None of these bill by the hour (the key is about "
                 "$1 a month).")
        tofu.init(var_file=var_file)
        changed = tofu.plan(out=sdir / "setup.tfplan", var_file=var_file, state=state)
        if changed:
            self.confirm("Create or update these setup resources?")
            tofu.apply(sdir / "setup.tfplan", state=state, var_file=var_file)
        (sdir / "setup.tfplan").unlink(missing_ok=True)
        summary = self.cloud.setup_summary(tofu.outputs(state=state, var_file=var_file))
        note = None if self.as_caller else self.cloud.prepare_operator(summary)
        summary["operator_profile_written"] = bool(note and note.startswith("Wrote"))
        self.home.save_setup_outputs(self.cloud.name, summary)
        if note:
            self.out(note)
        self.refresh_ssh_config()
        self.include_ssh_config()
        self.out("Setup is done. Next: infra/bin/cloud.py up")
        return summary

    def up(self, opts: Options, wait: bool = True) -> Dict[str, Any]:
        opts.validate()
        for other, record in self.home.all_machine_records().items():
            if other == opts.name and record.get("cloud") != self.cloud.name:
                raise UserError(f"'{opts.name}' already names a machine on {record.get('cloud')}; pick another --name")
        setup = self._setup()
        mdir = self.home.machine_dir(self.cloud.name, opts.name)
        previous = self.home.machine_record(self.cloud.name, opts.name)
        tofu = self._machine_tofu(setup, opts.name)
        tofu.check_version()
        key = self.keygen(mdir, f"caseforge-{opts.name}")
        opts = replace(opts, allowed_cidrs=self._resolve_cidrs(opts.allowed_cidrs))
        var_file = mdir / "machine.tfvars.json"
        write_json(var_file, self._vars(setup, opts, True, sshmod.public_key(key)))
        self._init(tofu, setup, opts.name, var_file)
        self.out(costs.describe(self.cloud, opts.size, opts.disk_gb))
        stop_note = (f"The machine stops itself {opts.auto_stop_hours}h after each start (data kept)."
                     if opts.auto_stop_hours else "Automatic stop is off: stop it yourself.")
        self.out(f"SSH will be open to {', '.join(opts.allowed_cidrs)} only. {stop_note}")
        self._plan_and_apply(tofu, mdir / "machine.tfplan", var_file,
                             f"Bring up '{opts.name}' on {self.cloud.label} ({opts.size}, stack '{opts.stack}')?")
        outputs = tofu.outputs(var_file=var_file)
        record = self._record(opts.name, opts, outputs["machine"], outputs.get("ssh_user", "caseforge"),
                              outputs.get("forwarded_ports", {}))
        self.refresh_ssh_config()
        self.include_ssh_config()
        self._report(record)
        if previous and previous.get("machine", {}).get("id") == record["machine"].get("id"):
            self._sync_settings(opts, previous.get("options", {}))
        if wait:
            ok = self.wait_until_ready(opts.name)
            self.out("Ready." if ok else "Not ready yet; follow it with: infra/bin/cloud.py logs")
        self._next_steps(opts.name)
        return record

    def _sync_settings(self, opts: Options, before: Mapping[str, Any]) -> None:
        """Cloud-init runs once; on an existing machine, a changed ref or stack is applied over SSH."""
        ssh = self._ssh(opts.name)
        if before.get("git_ref") != opts.git_ref:
            self.out(f"Switching the machine to {opts.git_ref}...")
            ssh.run(["caseforge", "update", opts.git_ref], timeout=15)
        if before.get("stack") != opts.stack:
            self.out(f"Switching the stack to {opts.stack}...")
            ssh.run(["caseforge", "stack", opts.stack], timeout=15)
        if before.get("auto_stop_hours") != opts.auto_stop_hours:
            ssh.run(["caseforge", "extend", str(opts.auto_stop_hours)], timeout=15)

    def _known(self, name: str) -> Dict[str, Any]:
        record = self.home.machine_record(self.cloud.name, name)
        if not record:
            raise UserError(f"this computer has no record of '{name}' on {self.cloud.label}: bring it up with "
                            f"`infra/bin/cloud.py up --name {name}` (it reuses an existing machine)")
        return record

    def set_running(self, name: str, running: bool, allow: Sequence[str] = (), wait: bool = True) -> Dict[str, Any]:
        record = self._known(name)
        setup = self._setup()
        opts = Options(**record["options"])
        if running:  # re-admit the address this computer has now: it may have changed since the last start
            opts = replace(opts, allowed_cidrs=self._resolve_cidrs(allow))
        mdir = self.home.machine_dir(self.cloud.name, name)
        tofu = self._machine_tofu(setup, name)
        var_file = mdir / "machine.tfvars.json"
        write_json(var_file, self._vars(setup, opts, running, sshmod.public_key(mdir / "id_ed25519")))
        self._init(tofu, setup, name, var_file)
        if running:
            self.out(costs.describe(self.cloud, opts.size, opts.disk_gb))
        verb = "Start" if running else "Stop"
        what = "compute bills again" if running else "the disk and all data are kept; only storage bills"
        self._plan_and_apply(tofu, mdir / "machine.tfplan", var_file, f"{verb} '{name}' ({what})?")
        outputs = tofu.outputs(var_file=var_file)
        record = self._record(name, opts, outputs["machine"], outputs.get("ssh_user", "caseforge"),
                              outputs.get("forwarded_ports", {}))
        self.refresh_ssh_config()
        self._report(record)
        if running and wait:
            ok = self.wait_until_ready(name, minutes=20)
            self.out("Ready." if ok else "Not ready yet; follow it with: infra/bin/cloud.py logs")
        if running:
            self._next_steps(name)
        return record

    def down(self, name: str) -> None:
        setup = self._setup()
        record = self.home.machine_record(self.cloud.name, name)
        mdir = self.home.machine_dir(self.cloud.name, name)
        mdir.mkdir(parents=True, exist_ok=True)
        opts = Options(**record["options"]) if record else Options(name=name)
        key_pub = mdir / "id_ed25519.pub"
        public = key_pub.read_text().strip() if key_pub.exists() else DESTROY_PLACEHOLDERS["ssh_public_key"]
        if not opts.allowed_cidrs:
            opts = replace(opts, allowed_cidrs=list(DESTROY_PLACEHOLDERS["allowed_cidrs"]))
        tofu = self._machine_tofu(setup, name)
        var_file = mdir / "machine.tfvars.json"
        write_json(var_file, self._vars(setup, opts, False, public))
        self._init(tofu, setup, name, var_file)
        self.out(f"This deletes '{name}' and everything created for it: the machine, its disk and every bit of data "
                 "on it, its network and its budget. Afterwards nothing for it bills.")
        changed = self._plan_and_apply(tofu, mdir / "machine.tfplan", var_file,
                                       f"Delete '{name}' for good?", destroy=True, expect=name)
        for leftover in ("machine.json", "machine.tfvars.json", "known_hosts", "id_ed25519", "id_ed25519.pub"):
            (mdir / leftover).unlink(missing_ok=True)
        self.refresh_ssh_config()
        self.out(f"'{name}' is gone." if changed else f"'{name}' did not exist; nothing to delete.")

    def teardown(self) -> None:
        """Delete every machine, then everything setup created, then this computer's setup files."""
        setup = self._setup()
        names = sorted(set(n for n, r in self.home.all_machine_records().items() if r.get("cloud") == self.cloud.name)
                       | {"caseforge"})
        self.out(f"This deletes, on {self.cloud.label} in {setup['region']}: every cloud machine ({', '.join(names)}) "
                 "with its disk and data, then everything setup created: the state store and the machines' state, the "
                 "operator and CI roles, and the state key (deleted after the cloud's shortest waiting period). "
                 "Afterwards nothing bills.")
        self.confirm("Tear everything down?", expect="delete everything")
        asked, self.assume_yes = self.assume_yes, True
        try:
            for name in names:
                self.down(name)
            sdir = self.home.setup_dir(self.cloud.name)
            tofu = self.tofu_factory(self.cloud.setup_root, sdir / "tofu", {})
            var_file = sdir / "setup.tfvars.json"
            write_json(var_file, self.cloud.teardown_vars(setup["region"]))
            state, plan = sdir / "terraform.tfstate", sdir / "setup.tfplan"
            tofu.init(var_file=var_file)
            self.out("Allowing the state store to be deleted with its contents...")
            if tofu.plan(out=plan, var_file=var_file, state=state):
                tofu.apply(plan, state=state, var_file=var_file)
            self.out("Deleting what setup created...")
            if tofu.plan(out=plan, var_file=var_file, state=state, destroy=True):
                tofu.apply(plan, state=state, var_file=var_file)
            plan.unlink(missing_ok=True)
        finally:
            self.assume_yes = asked
        (self.home.setup_dir(self.cloud.name) / "outputs.json").unlink(missing_ok=True)
        note = self.cloud.remove_operator()
        if note:
            self.out(note)
        self.refresh_ssh_config()
        self.out(f"Torn down: nothing CaseForge created on {self.cloud.label} is left (apart from the key, which the "
                 "cloud deletes at the end of its waiting period). `setup` starts again from nothing.")

    def status(self, name: str) -> int:
        record = self._known(name)
        machine = record["machine"]
        self._report(record)
        if not machine.get("running"):
            return 0
        ssh = self._ssh(name)
        if not ssh.reachable():
            self.out("Not reachable over SSH. It may have stopped itself (automatic stop) or your address changed: "
                     f"run `infra/bin/cloud.py start --name {name}`, which also re-admits your current address.")
            return 1
        return ssh.run(["caseforge", "status"], timeout=15).returncode

    def _report(self, record: Mapping[str, Any]) -> None:
        m = record["machine"]
        o = record["options"]
        state = "running" if m.get("running") else "stopped"
        self.out(f"{o['name']}: {state} on {m.get('cloud')} {m.get('region')}, {m.get('machine_type')} "
                 f"({o['size']}), stack '{o['stack']}' at {o['git_ref']}"
                 + (f", address {m['public_ip']}" if m.get("public_ip") else ""))
        if m.get("console_url"):
            self.out(f"  console: {m['console_url']}")

    def _next_steps(self, name: str) -> None:
        self.out("\n".join([
            "",
            f"  ssh {name}                     sign in; while it is open the app is on http://localhost:8000",
            "                                    (Keycloak :8080, Grafana :3000, Temporal :8233, Flink :8081)",
            f"  ssh {name} caseforge status    what is running;  caseforge logs app  follows the app",
            "  infra/bin/cloud.py stop        stop paying for compute, keep the data",
            "  infra/bin/cloud.py down        delete everything",
        ]))

