# The cloud machine: the whole platform on one cloud VM

One command sets an account up, one brings the full stack up, one takes it down. The machine runs exactly what a
laptop runs with `make full` (Postgres, the app, Kafka, the Iceberg lake, OpenSearch, Apache AGE, Temporal), on
8 vCPUs and 32 GB, and you use it from your laptop over SSH as if it were local: `ssh caseforge`, then open
<http://localhost:8000>.

This is the quick, cheap way to run the platform somewhere other than a laptop. The Kubernetes deployment with
managed services (the rest of `infra/`) is the production path and a separate decision.

```
infra/bin/cloud.py setup     # once per account, with an administrator's credentials
infra/bin/cloud.py up        # machine, Docker, the stack, the demo seed; waits until the app answers
ssh caseforge                # sign in; while it is open the app is http://localhost:8000
infra/bin/cloud.py stop      # stop paying for compute, keep the data
infra/bin/cloud.py start     # back again (new address, picked up automatically)
infra/bin/cloud.py down      # delete everything; nothing keeps billing
```

The same commands are `make cloud-setup`, `make cloud-up`, `make cloud-stop`, `make cloud-start`, `make cloud-down`
and `make cloud-status` (pass options with `ARGS="--size xlarge"`).

## What you need on the laptop

Very little, and nothing that runs the stack:

| Tool | Mac | Why |
|---|---|---|
| Python 3.9 or later | built in (`python3`) | the command itself; standard library only |
| OpenTofu 1.11 or later | `brew install opentofu` | creates and deletes the cloud resources |
| OpenSSH | built in | signing in and the tunnel to the consoles |
| AWS CLI (optional) | `brew install awscli` | only to sign in (`aws configure` or `aws sso login`) |

No Docker on the laptop: images build and run on the machine.

## Setup, once per account

`setup` applies `infra/stacks/aws/bootstrap` with local state (kept in `~/.caseforge/cloud/aws/setup`). It creates:

- the **state store**: a versioned, KMS-encrypted S3 bucket with public access blocked and TLS required, which holds
  each machine's OpenTofu state (also encrypted client-side). Because state lives in the account, a machine brought up
  from one computer can be stopped or deleted from another.
- the **state key** (KMS, about $1 a month).
- the **operator role** `caseforge-machine-operator`: what `up`, `stop`, `start` and `down` run as. It can manage the
  machine's network, instance, disk and budget in the allowed regions and read and write the machine's state, and it
  cannot create or change IAM. Setup writes a `caseforge-operator` profile to `~/.aws/config` that assumes it from
  the profile you signed in with, and later commands use it automatically (`--as-caller` uses your own credentials).
- the machine's **instance profile** (Session Manager only), so the operator never needs IAM write access.
- the GitHub OIDC roles the rest of `infra/` uses for CI plans and applies (no cost; see `infra/README.md`).

It also adds one `Include` line at the top of `~/.ssh/config` (with a backup of the original next to it) pointing at
`~/.caseforge/cloud/ssh/config`, which the command rewrites with a `Host` block per machine. That is what makes
`ssh caseforge` work.

What only the account owner can do: create or choose the AWS account, and sign the laptop in as an administrator
for `setup` (`aws configure`, or an IAM Identity Center profile with `aws sso login --profile <name>` and
`export AWS_PROFILE=<name>`). Never put access keys in the repository or in chat.

## Up

`up` generates an SSH key for the machine (the private half stays in `~/.caseforge/cloud/aws/machines/<name>/`),
finds this computer's public address, prints the cost, asks, then applies `infra/stacks/aws/machine`:

- a VPC of its own with one public subnet, and a security group that admits **SSH from your address only**. Nothing
  else is reachable from the internet; every console is reached through the SSH tunnel.
- one Ubuntu 24.04 EC2 instance (`m7i.2xlarge` for `large`), IMDSv2 only, with a 150 GB encrypted gp3 disk.
- cloud-init from `infra/modules/common/machine-bootstrap`, the same on every cloud: a `caseforge` user with your key
  (password login off), Docker from Docker's own repository, a clone of this repository at `--ref`, and a systemd
  service that runs `make <stack>` and restarts it on failure and on every boot.
- an **automatic stop** 10 hours after each start (`--auto-stop`, 0 for never), so a forgotten machine stops billing
  for compute. Stopping keeps the disk and data. `ssh caseforge caseforge extend 4` pushes it back.
- optionally a monthly **budget alert** by e-mail (`--budget-email you@example.com --budget 150`).

The first start builds every image and seeds the demo: about 10 to 20 minutes, which `up` waits for. Running `up`
again changes the existing machine in place: a new `--ref` or `--stack` is applied over SSH, and your current
address is re-admitted.

Options: `--size small|large|xlarge` (4/16, 8/32, 16/64 vCPU/GB), `--disk 150`, `--stack full|sso|observability|
lineage|streaming|backbone|demo`, `--ref main`, `--allow <cidr>` (repeatable, instead of the detected address),
`--name caseforge-<suffix>` for a second machine, `--no-wait`, `--yes` to skip the questions.

## Using it

```
ssh caseforge                         a shell; the consoles are forwarded while it is open:
                                        app http://localhost:8000   Keycloak :8080   Grafana :3000   Prometheus :9090
                                        Temporal :8233   Flink :8081   Jaeger :16686   Marquez :3001
ssh caseforge caseforge status        what is running, whether the app answers, when the machine stops itself
ssh caseforge caseforge logs app      follow one service (or all of them without a name)
ssh caseforge caseforge restart       restart the stack, keeping data
ssh caseforge caseforge stack sso     run another Makefile target (here: sign-in through Keycloak)
ssh caseforge caseforge update BRANCH run another branch, tag or commit
ssh caseforge caseforge reset --yes   wipe every volume and start from a fresh seed
```

The same through the command: `infra/bin/cloud.py status | logs [service] | update REF | stack NAME | extend HOURS`.
Because the consoles are on `localhost`, `stack sso` works unchanged: Keycloak's issuer is
`http://localhost:8080`, as on a laptop.

Secrets such as `ANTHROPIC_API_KEY` go into the stack's `.env` on the machine, never into OpenTofu or user data:

```
grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | infra/bin/cloud.py env ANTHROPIC_API_KEY
ssh caseforge caseforge restart
```

The value travels on SSH's standard input, so it is not in any process list or shell history.

## What each way of stopping keeps and costs

Prices are on-demand list prices in us-east-1; `infra/bin/cloud.py cost` prints them for every size.

| | Keeps | Bills | `large`, 150 GB |
|---|---|---|---|
| running | everything | compute, disk, public IPv4 | about $0.42 an hour, $3.70 for an 8-hour day |
| `stop` (or the automatic stop) | the machine, its disk, all data | the disk only | about $12 a month |
| `down` | nothing: machine, disk, data, network and budget are deleted | nothing | $0 |
| setup (stays after `down`) | state bucket, key, roles | the key and a few KB of state | about $1 a month |

`down` asks you to type the machine's name. It needs only the setup on this computer, not the machine's own files,
so it works from any computer that ran `setup` against the same account.

## Safety

- SSH is the only open port, and only from the addresses you allow; the module refuses `0.0.0.0/0`. Docker publishes
  the stack's ports on the machine, and the security group is what keeps them private, so do not open more ports
  there. To share the app with someone, give them an SSH key, not a firewall rule.
- Key-only SSH for one user; root login and passwords are off.
- The disk is encrypted; IMDSv2 only, with a hop limit of 1 so containers cannot reach the instance's credentials.
- User data and OpenTofu variables hold no secrets; state is encrypted with the account's KMS key before it is sent.
- The stack keeps its laptop defaults (`caseforge-local-only` passwords, the persona picker): it is a private
  demonstration machine reachable only through your tunnel, not a production deployment.

## Layout, and adding Azure or Google Cloud

```
infra/contract/machine-variables.tf     the machine contract: variables every cloud's machine module takes
infra/contract/machine-outputs.md       ...and the keys of its one `machine` output (contract/check.py enforces both)
infra/modules/common/machine-bootstrap  cloud-init and the on-machine `caseforge` command: identical on every cloud
infra/modules/aws/machine               EC2, VPC, security group, instance state (stop and start), budget
infra/modules/aws/machine-operator      the operator role and the machine's instance profile (part of setup)
infra/stacks/aws/machine                the root module `up` applies (S3 state, KMS-encrypted)
infra/cloud/caseforge_cloud             the command: lifecycle, SSH, OpenTofu wrapper, one class per cloud
```

A new cloud needs `infra/modules/<cloud>/machine` implementing the contract (Ubuntu 24.04 with the bootstrap's user
data, a firewall admitting SSH from `allowed_cidrs` only, stop and start through `running`),
`infra/stacks/<cloud>/machine`, and a `Cloud` class in `infra/cloud/caseforge_cloud/clouds/<cloud>.py` (state
backend, prices, the operator identity). The bootstrap, SSH, waiting and the command are shared.

## Tests

Nothing here needs a cloud account to test:

```
python3 -m unittest discover -s infra/cloud/tests -t infra/cloud/tests     # the command and the on-machine script
make infra-validate infra-test                                              # includes the machine modules' mocked tests
```

The first apply to a real account will find what mocks cannot (quotas, for example: new accounts may need a vCPU
quota increase for on-demand standard instances before an 8-vCPU machine starts).
