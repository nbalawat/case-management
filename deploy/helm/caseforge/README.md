# CaseForge on Kubernetes

A Helm chart for the application tier of one stamp: the API and UI, the backbone worker and the durable worker.
One release is one stamp, for a single client (dedicated) or a pool of clients (SaaS).

**What the chart does not deploy:** Postgres, Kafka, OpenSearch, Temporal, Flink, a graph engine, object storage,
an identity provider, an ingress controller or certificates. Each is addressed by a URL in the values. Run them with
their own operators (CloudNativePG, Strimzi, the OpenSearch, Temporal and Flink operators) or use managed services.
That is deliberate: those are stateful systems with their own upgrade and backup stories, and they differ by cloud.

## Install

    kubectl create namespace acme
    kubectl -n acme create secret generic caseforge-secrets \
      --from-literal=DATABASE_URL='postgresql+psycopg://user:password@host:5432/caseforge'
    helm install acme deploy/helm/caseforge -n acme -f deploy/helm/caseforge/modes/dedicated.yaml \
      --set existingSecret=caseforge-secrets \
      --set image.repository=registry.example.com/caseforge --set image.tag=0.25.0 \
      --set publicUrl=https://cases.acme.example --set ingress.host=cases.acme.example \
      --set auth.operatorIssuer=https://idp.example.com/realms/ops \
      --set storage.evidenceUrl=s3://acme-caseforge/evidence --set storage.quarantineUrl=s3://acme-caseforge/quarantine

The chart never writes a secret. Every key of the Secret you name becomes an environment variable, so it can also
carry `ANTHROPIC_API_KEY`, `KAFKA_CONFIG`, `SEARCH_CONFIG`, `GRAPH_URL` and `LAKE_CATALOG`.

Turn the rest on by giving an address: `eventBus.url` adds the backbone worker, `temporal.url` adds the durable
worker, `search.url` and `flink.url` only change what the application talks to.

## The four deployment modes

The same chart, with a values file per mode in `modes/`:

| Mode | File | What differs |
|---|---|---|
| Pooled SaaS | `saas.yaml` | More replicas, autoscaling, many clients in one database separated by row |
| Dedicated stamp | `dedicated.yaml` | One release per client in its own namespace, with its own database, topics and bucket |
| Client cloud | `client-cloud.yaml` | The client's registry and the client's workload identity annotation; nothing names a cloud |
| Air-gapped | `air-gapped.yaml` | The site's registry, no model called, nothing reaches out |

## What it refuses

A release fails at install, with the reason, when: no Secret is named; `schemaOnMismatch=rebuild` is set outside a
demo; demo sign-in would be exposed through an ingress outside a demo; no evidence storage is given outside a demo;
or an address has the wrong scheme.

## Before you rely on it

- **Schema changes are versioned migrations** (`backend/app/migrations`). A hook job runs them before each install
  and upgrade, and a pod that finds the database behind stops and says so. That has been run on Postgres 16 outside
  Kubernetes; the hook job itself has never run on a cluster. There is one migration so far, the baseline, so no
  upgrade between two real versions has happened yet, and there are no downgrade tests. Take a backup first.
- The pipeline schedule timer is off, because it is wrong with more than one API pod. Scheduled pipelines run when
  "Run what is due" is called; nothing calls it for you here.
- Pods run as a non-root user with no privilege escalation and a read-only root filesystem, and write only to `/tmp`
  and `/app/data`, both scratch volumes that go with the pod.
- `networkPolicy.enabled` limits what may reach the pods. It does not limit what the pods may reach.
- One release serves the clients in its database. A dedicated stamp per client is one release per client; nothing
  here creates the client's database, topics or bucket.
- No control plane: onboarding a stamp, metering and release rings across stamps are not built.

## Cloud identity and extra mounts

Three values exist so the infrastructure code (`infra/`) can wire a stamp to its cloud without forking the chart:

| Value | Used for |
|---|---|
| `serviceAccount.annotations` | Binding the pods to the stamp's cloud identity (IRSA on EKS, workload identity on AKS and GKE) |
| `podLabels` | Labels on every pod; Azure workload identity needs `azure.workload.identity/use: "true"` |
| `extraVolumes`, `extraVolumeMounts` | Mounting a CA bundle, for example the in-cluster OpenSearch's CA from `search-http-tls` |

On a cloud, nobody runs `helm install` by hand: `infra/modules/kubernetes/stamp-release` creates the namespace, its
policies and secrets, and an Argo CD application that installs this chart with the image tag from
`deploy/environments/<env>/<stamp>.yaml`. See `infra/README.md`. In-cluster Kafka and OpenSearch for a stamp come
from the sibling chart `deploy/helm/caseforge-data`; cluster-wide policies from `deploy/helm/caseforge-policies`.

## What has and has not been run

`.github/workflows/deploy-test.yml` installs this chart on a kind cluster with the caseforge Kyverno policies
enforced and the namespace held to the restricted pod security standard, waits for the migration hook and the pods,
calls the API, and upgrades the release. A second job brings up `caseforge-data` (Kafka and OpenSearch through their
operators) on kind. `deploy/scripts/check-charts.sh` lints and renders every chart and checks every object strictly
against the Kubernetes and operator CRD schemas.


See the "Kubernetes" row in the repository README for exactly what was run and where.
