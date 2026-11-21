#!/usr/bin/env bash
# Lint and render every chart, then check every rendered object against the Kubernetes and CRD schemas, strictly.
#   deploy/scripts/check-charts.sh <crd schema dir>
# RENDER_DIR=<dir> keeps the rendered manifests there, for a scanner to read afterwards.
set -euo pipefail
schemas="${1:?crd schema dir (deploy/scripts/crd-schemas.sh)}"
cd "$(dirname "$0")/../helm"
KUBE=1.33.0
if [ -n "${RENDER_DIR:-}" ]; then
  render="$RENDER_DIR"; mkdir -p "$render"
else
  render="$(mktemp -d)"; trap 'rm -rf "$render"' EXIT
fi
APP=(--set existingSecret=caseforge-secrets --set storage.evidenceUrl=s3://bucket/evidence --set ingress.host=cases.example.com --set keys.transit.addr=https://openbao:8200)

helm lint caseforge "${APP[@]}" --kube-version $KUBE
helm lint caseforge-data --kube-version $KUBE
helm lint caseforge-policies --kube-version $KUBE

for mode in saas dedicated client-cloud air-gapped; do
  helm template stamp caseforge -f caseforge/modes/$mode.yaml "${APP[@]}" --kube-version $KUBE \
    --set eventBus.url=kafka://kafka:9093 --set temporal.url=temporal://temporal:7233 --set search.url=opensearchs://search:9200 \
    --set podLabels.azure\\.workload\\.identity/use=true \
    --set 'extraVolumes[0].name=search-ca' --set 'extraVolumes[0].secret.secretName=search-http-tls' \
    --set 'extraVolumeMounts[0].name=search-ca' --set 'extraVolumeMounts[0].mountPath=/etc/caseforge/search-ca' \
    > "$render/app-$mode.yaml"
done
helm template data caseforge-data --kube-version $KUBE -n cf-acme > "$render/data.yaml"
helm template data caseforge-data --kube-version $KUBE -n cf-acme --set kafka.enabled=false > "$render/data-search-only.yaml"
helm template policies caseforge-policies --kube-version $KUBE --set clusterIssuer.acmeEmail=ops@example.com \
  --set signedImages.enabled=true --set 'signedImages.image_patterns[0]=ghcr.io/nbalawat/caseforge*' \
  --set signedImages.subject_regexp='^https://github.com/nbalawat/case-management/' > "$render/policies.yaml"

kubeconform -strict -summary -kubernetes-version $KUBE \
  -schema-location default -schema-location "$schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  "$render"/*.yaml

# The application's pods must meet the restricted pod security standard as rendered.
for f in "$render"/app-*.yaml; do
  python3 - "$f" <<'PY'
import sys, yaml
bad = []
for doc in yaml.safe_load_all(open(sys.argv[1])):
    if not doc or doc.get("kind") not in ("Deployment", "Job"):
        continue
    spec = doc["spec"]["template"]["spec"]
    pod = spec.get("securityContext", {})
    for c in spec["containers"]:
        sc = c.get("securityContext", {})
        name = f'{doc["kind"]}/{doc["metadata"]["name"]}/{c["name"]}'
        if not pod.get("runAsNonRoot"): bad.append(f"{name}: runAsNonRoot")
        if pod.get("seccompProfile", {}).get("type") != "RuntimeDefault": bad.append(f"{name}: seccomp")
        if sc.get("allowPrivilegeEscalation") is not False: bad.append(f"{name}: allowPrivilegeEscalation")
        if "ALL" not in sc.get("capabilities", {}).get("drop", []): bad.append(f"{name}: drop ALL")
        if sc.get("readOnlyRootFilesystem") is not True: bad.append(f"{name}: readOnlyRootFilesystem")
if bad:
    sys.exit("not restricted: " + ", ".join(bad))
PY
done
# Custom resources must use the exact kind their CRD declares, case included.
python3 - "$schemas/kinds.txt" "$render"/*.yaml <<'PY'
import sys, yaml
known = {}
for line in open(sys.argv[1]):
    group, kind = line.split()
    known.setdefault(group, set()).add(kind)
bad = []
for f in sys.argv[2:]:
    for doc in yaml.safe_load_all(open(f)):
        if not doc:
            continue
        group = doc["apiVersion"].split("/")[0] if "/" in doc["apiVersion"] else ""
        if group in known and doc["kind"] not in known[group]:
            bad.append(f'{doc["apiVersion"]} {doc["kind"]} ({doc["metadata"]["name"]})')
if bad:
    sys.exit("kinds not declared by their CRDs: " + ", ".join(bad))
PY
echo "charts: lint, render, schema, kind and pod security checks passed"
