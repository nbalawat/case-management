# CaseForge infrastructure as code

OpenTofu for AWS, Azure and Google Cloud behind one cloud-neutral interface, plus the in-cluster layer (operators,
policies, GitOps) that is the same on every cloud. Together with the Helm charts in `deploy/helm` and the workflows in
`.github/workflows`, it takes the platform from an empty cloud account to running stamps, pooled or dedicated.

> **Status, said plainly.** Everything here has been formatted, validated, unit-tested against mocked providers,
> linted and security-scanned (details under [What has been proven](#what-has-been-proven)). **None of it has ever
> been applied to a real cloud account.** The first apply to each cloud will find problems the checks cannot,
> such as quota limits, region availability, provider behaviour the mocks do not model and organisation policies.
> Plan on a dev subscription or project first.

## Decisions, and why

| Decision | Why |
|---|---|
| **OpenTofu** (>= 1.11), not Terraform | Open source (MPL, Linux Foundation). It encrypts state and plans on the client (`aws_kms`, `gcp_kms`, `pbkdf2`), which Terraform does not, and supports ephemeral values and write-only arguments, so database and broker passwords never reach state. Modules stay Terraform-compatible apart from the `encryption` block. |
| **One stamp contract for all clouds** (`contract/`) | A stamp is "the cloud resources for one release": database, buckets, keys, identity, optionally managed Kafka and search. Every cloud's stamp module takes the same variables and returns one `contract` output of the same shape, so the Kubernetes layer (`modules/kubernetes/stamp-release`) never names a cloud. `contract/check.py` fails CI if the copies drift. |
| **Layered stacks per cloud**: bootstrap, platform, addons, stamp | Separate state, separate blast radius and separate cadence: the platform (network, cluster, registry) changes rarely, add-ons occasionally, and stamps every time a client is onboarded. A bad stamp plan cannot touch the cluster. |
| **Kubernetes everywhere** (EKS, AKS, GKE), private API servers | The application is already a Helm chart; Kubernetes is the cloud-neutral runtime. Clusters have private endpoints, workload identity, encrypted secrets with a customer-managed key, and no local accounts. |
| **Managed or in-cluster, per stamp** for Kafka and search | `kafka.mode` and `search.mode` are `managed` (MSK, Event Hubs' Kafka endpoint, OpenSearch Service) or `in_cluster` (Strimzi, the OpenSearch operator). Dev and the pool run in-cluster to save cost; a large dedicated client can use managed services. GCP has no managed Kafka or OpenSearch here, and Azure no managed OpenSearch: those stamps use `in_cluster`, and the stamp module refuses otherwise. |
| **No secrets in Git, in values or in state** | Passwords are generated as ephemeral values and written with write-only arguments, or managed by the cloud (RDS-managed master password). The External Secrets Operator reads them from the cloud's secret manager with the stamp's own identity and builds `DATABASE_URL` and `KAFKA_CONFIG` inside the cluster. |
| **GitOps with Argo CD** | Each stamp is an Argo CD project and applications pointing at this repository. The image tag a stamp runs is a line in `deploy/environments/<env>/<stamp>.yaml`, changed only by pull request: releases and promotions are reviewable, revertible commits. |
| **CI with OIDC only** | GitHub Actions signs in to each cloud through workload identity federation. There are no stored cloud keys or client secrets. Plans use a read-only identity; applies use a per-environment identity behind a protected GitHub environment and apply exactly the reviewed plan. |
| **Policy as code in the cluster** | Kyverno enforces the restricted pod security standard on application pods, baseline on data pods, no `latest` tags, and (optionally) only images signed by this repository's release workflow. |
| **Traefik** as the ingress controller | ingress-nginx is being retired by its maintainers. Traefik is open source and implements both Ingress and Gateway API. |

The cloud service mapping follows the platform guide ("Cloud deployment" section): EKS, AKS, GKE; RDS, Azure
Database for PostgreSQL Flexible Server, Cloud SQL; S3, ADLS Gen2, GCS; MSK, Event Hubs; OpenSearch Service; KMS, Key
Vault, Cloud KMS; ECR, ACR, Artifact Registry.

## Layout

```
infra/
  contract/              the stamp contract: stamp-variables.tf (copied into each cloud's stamp), stamp-outputs.md, check.py
  modules/
    aws/                 kms, network, cluster (EKS), registry, postgres (RDS), object-store (S3), kafka (MSK),
                         search (OpenSearch Service), workload-identity (IRSA), platform, stamp
    azure/               kms (Key Vault), network, cluster (AKS), registry (ACR), postgres (Flexible Server),
                         object-store (ADLS Gen2), kafka (Event Hubs), workload-identity, platform, stamp
    gcp/                 kms, network, cluster (GKE), registry (Artifact Registry), postgres (Cloud SQL),
                         object-store (GCS), workload-identity, platform, stamp
    kubernetes/
      platform-addons    cert-manager, External Secrets, Traefik, Argo CD, Kyverno and the caseforge policies,
                         Strimzi, the OpenSearch and Flink operators, kube-prometheus-stack
      stamp-release      one stamp in the cluster: namespace, quota, network policies, service accounts, secret
                         stores and secrets, and the Argo CD project and applications (or a direct Helm release)
  stacks/<cloud>/        root modules: bootstrap, platform, addons, stamp
  envs/<cloud>/<env>/    backend.hcl, common.tfvars, <layer>.tfvars, stamps/<stamp>.tfvars, for dev, staging, prod
  bin/stack.py           runs one layer of one environment, the same way on a laptop and in CI
  bin/each.sh            validate, test or lock every root
```

Modules are small and single-purpose; the `platform` and `stamp` modules compose them. Modules declare provider
requirements but never configure providers; only stacks do.

## Pooled and dedicated stamps

A **stamp** is one release of the application plus its data: one database, its buckets, its key, its identity and
its Kubernetes namespace.

- **Pooled** (`tenancy = "pooled"`, chart mode `saas`): the managed-service stamp. Many smaller clients share it and
  are separated by row in one database, as the application already does. Onboarding a client is application
  configuration, not infrastructure.
- **Dedicated** (`tenancy = "dedicated"`, chart mode `dedicated`): one client per stamp, with its own database,
  buckets, encryption key, cloud identity and namespace. Onboarding is a new `stamps/<client>.tfvars` and one apply.

Both kinds run on the same shared platform cluster per environment. A client that needs its own cluster, account or
subscription gets its own platform stack (copy `envs/<cloud>/<env>` to a new directory, such as `prod-acme`).

## Running it

Tools: OpenTofu 1.11, Python 3.11 or later; TFLint and Checkov for the checks.

```
infra/bin/stack.py <cloud> <env> <layer> [--stamp NAME] init|plan|apply|show|output|destroy [-- extra tofu args]
```

`plan` writes an encrypted plan file; `apply` applies only that file, never a fresh plan.

### One-time bootstrap per cloud

The bootstrap stack creates the state store (versioned, encrypted with a customer-managed key, public access
blocked) and the GitHub OIDC identities. It runs once, from a laptop, with an administrator's credentials:

1. Replace every `CHANGE_ME` in `envs/<cloud>/*/` (account or subscription ids, project, region, hostnames, e-mails).
2. `infra/bin/stack.py <cloud> dev bootstrap init`, then `plan`, read the plan, then `apply`.
3. Copy the outputs into `envs/<cloud>/<env>/backend.hcl` and `common.tfvars` (state bucket, state key), then move
   the bootstrap's own state into that bucket (`tofu init -migrate-state` with the backend added).
4. In GitHub, set repository variables for the read-only plan identity and, for each environment (`dev`, `staging`,
   `prod`), create a GitHub environment with required reviewers and the apply identity's variables:

| Cloud | Repository variables (plan) | Environment variables (apply) |
|---|---|---|
| AWS | `AWS_PLAN_ROLE_ARN`, `AWS_REGION` | `AWS_ROLE_ARN` |
| Azure | `AZURE_PLAN_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | `AZURE_CLIENT_ID` |
| GCP | `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_PLAN_SERVICE_ACCOUNT` | `GCP_SERVICE_ACCOUNT` |

   Azure state is encrypted with a passphrase (Key Vault is not an OpenTofu key provider): store one per
   environment as repository secrets `STATE_PASSPHRASE_DEV`, `STATE_PASSPHRASE_STAGING`, `STATE_PASSPHRASE_PROD`,
   at least 16 characters. Keep a copy in your vault: without it the state cannot be read.

The plan identity is trusted for pull requests and for `main`; each apply identity only for its GitHub environment.
On Azure, the apply identity may create role assignments but never Owner, Contributor, User Access Administrator or
RBAC Administrator (an ABAC condition on its assignment). On AWS, set `permissions_boundary_arn` to cap it further.

### Then, per environment

Run `.github/workflows/infra-apply.yml` (Actions, "Infra apply") for each layer in order:

1. `platform`: network, cluster, registry, keys.
2. `addons`: the operators, Argo CD, Traefik, Kyverno and the policies. Start with `policy_action = "Audit"`, then
   switch to `Enforce` once nothing is reported.
3. `stamp`, once per stamp (`pool`, `acme`, ...): the stamp's cloud resources and its namespace and Argo CD apps.

Each run plans with the read-only identity, shows the plan in the run summary, waits for the environment's reviewers,
then applies exactly that plan. The `addons` and `stamp` layers reach the private cluster API, so run them on a
self-hosted runner inside the network or open `private_api_allowed_cidrs` to your runner's range; GitHub's hosted
runners cannot reach a private endpoint.

To plan on every pull request as well, set the repository variable `INFRA_PLAN_TARGETS`, for example
`[{"cloud":"aws","env":"dev","layer":"platform"}]`.

### Releases

1. Bump `appVersion` in `deploy/helm/caseforge/Chart.yaml`, merge, and push a tag `v<version>`.
2. `release.yml` builds the image, scans it with Trivy, pushes it to GHCR, signs it keyless with cosign, attaches an
   SPDX SBOM and build provenance, and opens a pull request setting the tag on the dev stamps.
3. Merge it; Argo CD syncs dev. `promote.yml` (dev to staging, staging to prod) verifies the signature and opens
   the next pull request. Prod stamps have `auto_sync = false`: a person syncs them in Argo CD.

Pull requests opened with the workflow token do not trigger other workflows. If CI must run on them, give
`create-pull-request` a GitHub App token instead.

## Security controls

- **State and plans** encrypted client-side by OpenTofu (`enforced = true`), on top of encrypted, versioned,
  private buckets with locking (S3 lock files, blob leases, GCS locks).
- **Identity**: OIDC from CI, no long-lived keys. Workload identity for every pod that calls a cloud API, one
  identity per stamp scoped to that stamp's buckets, secrets and key.
- **Encryption**: a customer-managed key per platform and per stamp, with rotation, for disks, databases, buckets,
  secrets, Kubernetes secrets, logs and the registry, where the service supports it.
- **Network**: private cluster APIs, private database and search endpoints, no public IPs on nodes, default-deny
  network policies per stamp namespace with the cloud metadata endpoints blocked, TLS to Postgres and Kafka.
- **Data**: optional WORM evidence buckets (S3 Object Lock, immutable blob policies, GCS retention policies) with a
  retention period, versioning, access logs, deletion protection on production databases, point-in-time recovery.
- **Workloads**: restricted pod security (non-root, read-only root filesystem, no privilege escalation, all
  capabilities dropped, seccomp), resource quotas, pinned images, signed-image admission.
- **Supply chain**: every GitHub Action pinned to a commit SHA, `persist-credentials: false`, least-privilege
  `permissions` per job, Dependabot for actions, providers, base images and packages, SBOMs and provenance.
- **Scanning in CI**: Checkov on the IaC (every skip in the code says why), TFLint with the cloud rulesets, Trivy on
  the rendered manifests and the image, strict schema validation of every rendered object.

## What has been proven

Run in this repository's CI (`.github/workflows/infra.yml`, `deploy-test.yml`) and before the pull request:

| Check | Result |
|---|---|
| `tofu fmt -check`, `tofu validate` | 44 roots (every module and stack) |
| `tofu test` with mocked providers | 11 modules: the three stamps, the clusters, object stores, RDS and the stamp release. They assert the contract's shape, private endpoints, encryption, WORM settings, network policy and secret wiring |
| Stamp contract check | Variables byte-identical on all three clouds, output keys identical |
| Checkov | 0 failed; each skipped check is skipped inline with its reason |
| TFLint (aws, azurerm, google rulesets) | clean |
| Charts: lint, render, kubeconform strict against Kubernetes 1.33 and the operators' CRD schemas | every mode, the data chart and the policy chart |
| Trivy config scan of the rendered charts | no HIGH or CRITICAL |
| Chart installed on kind with policies enforced, migrated, served, upgraded | in CI, `deploy-test.yml` |
| Kafka (Strimzi) and OpenSearch (operator) from the data chart becoming ready on kind | in CI, `deploy-test.yml` |

## What has never run

- **No `apply` against any real AWS, Azure or GCP account.** No plan against one either: plans need credentials.
- The release, promote and apply workflows have never run (they need a tag, GitHub environments and cloud
  identities). They pass actionlint and shellcheck.
- The platform add-ons have never been installed from OpenTofu; the kind job installs the same charts with Helm.
- The application has never been run against managed Kafka (MSK with SCRAM, Event Hubs), managed OpenSearch, an
  ADLS or GCS evidence store, or workload identity.

## Known gaps

- **Temporal and the graph engine (Apache AGE) are not provisioned.** Stamps take `temporal_url` and an optional
  `GRAPH_URL` secret; run Temporal Cloud or the Temporal Helm chart, and AGE on a self-managed Postgres (no managed
  Postgres offers AGE today).
- **Provider lock files are not committed**: the registry was unreachable where this was written. Run
  `make infra-lock` once and commit the `.terraform.lock.hcl` files in `infra/stacks/*/*`.
- Azure Postgres with a customer-managed key has geo-redundant backup off (it needs a second key and identity in
  the paired region). Evidence buckets are not replicated across regions on any cloud.
- S3 Object Lock buckets need upload checksums; check the application's S3 client sends them before turning WORM
  on. Event Hubs topic auto-creation, and the application reading `AZURE_STORAGE_ACCOUNT_NAME` for ADLS, are untested.
- The managed OpenSearch master user's password is in (encrypted) state; the domain has no write-only argument yet.
- Chart versions for the add-ons were pinned from the projects' release tags; check them against the chart
  repositories on first install. GKE shows a provider deprecation warning for `remove_default_node_pool`.
- No control plane: onboarding a stamp is a tfvars file and a workflow run, not an API.
