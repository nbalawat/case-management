"""infra/bin/cloud.py: the CaseForge cloud machine from one command.

  setup    once per account: encrypted state store, state key, least-privilege operator role, SSH config include
  up       create the machine (or update it) and start the whole stack; waits until the app answers
  stop     stop the machine, keep the disk and data (only storage bills)
  start    start it again (re-admits your current address)
  down     delete the machine and everything created for it (nothing keeps billing)
  teardown down for every machine, then delete everything setup created too (the account is left as it was)
  status   where it is, what it costs, and what is running on it
  ssh      sign in with every console forwarded to localhost (same as `ssh caseforge`)
  logs     follow the stack's logs (or one service's)
  update   run another branch, tag or commit;  stack: switch the Makefile target;  extend: push the auto-stop back
  env      set a value in the stack's .env on the machine (read from stdin, never from the command line)
  cost     what each size costs, running and stopped
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import costs
from .clouds import get_cloud
from .clouds.base import NotSupported
from .home import Home
from .lifecycle import SIZES, STACKS, Lifecycle, Options, UserError
from .tofu import ToolError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="infra/bin/cloud.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cloud", default=os.environ.get("CASEFORGE_CLOUD", "aws"), help="aws (azure and gcp: not yet)")
    common.add_argument("--name", default=os.environ.get("CASEFORGE_MACHINE", "caseforge"),
                        help="machine name, also the SSH host alias (default: caseforge)")
    common.add_argument("--yes", "-y", action="store_true", help="do not ask before changing anything")
    common.add_argument("--as-caller", action="store_true",
                        help="use your own credentials instead of the setup's operator role")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("setup", parents=[common], help="one-time account setup")
    p.add_argument("--region", help="region for the state store and the machine (default: AWS_REGION or us-east-1)")

    for name, helptext in (("up", "create or update the machine and start the stack"),):
        p = sub.add_parser(name, parents=[common], help=helptext)
        p.add_argument("--size", choices=SIZES, default="large", help="small 4 vCPU/16 GB, large 8/32 (default), xlarge 16/64")
        p.add_argument("--disk", type=int, default=150, dest="disk_gb", help="disk in GB (default 150)")
        p.add_argument("--stack", choices=STACKS, default="full", help="Makefile target to run (default full)")
        p.add_argument("--ref", default="main", dest="git_ref", help="branch, tag or commit (default main)")
        p.add_argument("--auto-stop", type=int, default=10, dest="auto_stop_hours",
                       help="hours after each start until the machine stops itself; 0 for never (default 10)")
        p.add_argument("--allow", action="append", default=[], metavar="CIDR",
                       help="address allowed to SSH (repeatable); default: this computer's public address")
        p.add_argument("--budget-email", default="", help="e-mail a monthly budget alert here")
        p.add_argument("--budget", type=float, default=150, dest="budget_usd", help="monthly budget in USD (default 150)")
        p.add_argument("--no-wait", action="store_true", help="return once the machine exists")

    p = sub.add_parser("start", parents=[common], help="start a stopped machine")
    p.add_argument("--allow", action="append", default=[], metavar="CIDR")
    p.add_argument("--no-wait", action="store_true")
    sub.add_parser("stop", parents=[common], help="stop the machine, keep its data")
    sub.add_parser("down", parents=[common], help="delete the machine and its data")
    sub.add_parser("teardown", parents=[common], help="delete every machine and everything setup created")
    sub.add_parser("status", parents=[common], help="show the machine and its stack")
    p = sub.add_parser("ssh", parents=[common], help="sign in, consoles forwarded to localhost")
    p.add_argument("remote", nargs=argparse.REMAINDER, help="optional command to run instead of a shell")
    p = sub.add_parser("logs", parents=[common], help="follow the stack's logs")
    p.add_argument("service", nargs="?", help="one service, for example app or keycloak")
    p = sub.add_parser("update", parents=[common], help="run another branch, tag or commit")
    p.add_argument("ref")
    p = sub.add_parser("stack", parents=[common], help="switch the Makefile target")
    p.add_argument("target", choices=STACKS)
    p = sub.add_parser("extend", parents=[common], help="push the automatic stop back")
    p.add_argument("hours", type=int, nargs="?", default=10)
    p = sub.add_parser("env", parents=[common], help="set KEY in the machine's .env from stdin")
    p.add_argument("key", help="for example ANTHROPIC_API_KEY; the value is read from stdin")
    p = sub.add_parser("cost", parents=[common], help="price of each size")
    p.add_argument("--disk", type=int, default=150, dest="disk_gb")
    return parser


def _remote(life: Lifecycle, name: str, command: Sequence[str], tty: bool = False, stdin: Optional[str] = None) -> int:
    life._known(name)
    return life.ssh_factory(life.home.ssh_config, name).run(list(command), timeout=15, tty=tty, stdin=stdin).returncode


def run(argv: Sequence[str], life: Optional[Lifecycle] = None) -> int:
    args = build_parser().parse_args(list(argv))
    cloud = get_cloud(args.cloud)
    life = life or Lifecycle(cloud=cloud, home=Home.default(), assume_yes=args.yes, as_caller=args.as_caller)
    if not life.assume_yes and not sys.stdin.isatty() and args.command in ("setup", "up", "start", "stop", "down", "teardown"):
        raise UserError("not a terminal: pass --yes to go ahead without being asked")
    cmd = args.command
    if cmd == "setup":
        if cloud.credentials_hint:
            life.out(cloud.credentials_hint)
        life.setup(args.region)
    elif cmd == "up":
        opts = Options(name=args.name, size=args.size, disk_gb=args.disk_gb, stack=args.stack, git_ref=args.git_ref,
                       auto_stop_hours=args.auto_stop_hours, allowed_cidrs=list(args.allow),
                       budget_email=args.budget_email, budget_usd=args.budget_usd)
        life.up(opts, wait=not args.no_wait)
    elif cmd == "start":
        life.set_running(args.name, True, allow=args.allow, wait=not args.no_wait)
    elif cmd == "stop":
        life.set_running(args.name, False)
    elif cmd == "down":
        life.down(args.name)
    elif cmd == "teardown":
        life.teardown()
    elif cmd == "status":
        return life.status(args.name)
    elif cmd == "ssh":
        life._known(args.name)
        argv_ssh: List[str] = life.ssh_factory(life.home.ssh_config, args.name).interactive(args.remote)
        os.execvp(argv_ssh[0], argv_ssh)
    elif cmd == "logs":
        return _remote(life, args.name, ["caseforge", "logs"] + ([args.service] if args.service else []), tty=True)
    elif cmd == "update":
        return _remote(life, args.name, ["caseforge", "update", args.ref])
    elif cmd == "stack":
        return _remote(life, args.name, ["caseforge", "stack", args.target])
    elif cmd == "extend":
        return _remote(life, args.name, ["caseforge", "extend", str(args.hours)])
    elif cmd == "env":
        value = sys.stdin.readline().rstrip("\n")
        if not value:
            raise UserError(f"pipe the value in, for example: grep ^{args.key}= .env | cut -d= -f2- | infra/bin/cloud.py env {args.key}")
        # The value travels on SSH's stdin, so it never appears in a process list on either side.
        script = f'read -r v; caseforge env "{args.key}=$v"'
        return _remote(life, args.name, ["bash", "-c", script], stdin=value + "\n")
    elif cmd == "cost":
        for size in SIZES:
            life.out(costs.describe(cloud, size, args.disk_gb))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (UserError, ToolError, NotSupported) as e:
        print(f"cloud: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncloud: interrupted", file=sys.stderr)
        return 130
