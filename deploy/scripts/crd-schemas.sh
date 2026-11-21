#!/usr/bin/env bash
# Build JSON schemas from the CRDs of the operators the charts target, at the versions the platform add-ons pin, so
# kubeconform can check custom resources strictly (unknown fields are errors).
#   deploy/scripts/crd-schemas.sh <output dir>
set -euo pipefail
out="${1:?output dir}"
mkdir -p "$out"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch() { # repo tag path...
  local repo=$1 tag=$2; shift 2
  git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" --filter=blob:none --sparse "https://github.com/$repo" "$work/$(basename "$repo")"
  git -C "$work/$(basename "$repo")" sparse-checkout set "$@"
}
fetch strimzi/strimzi-kafka-operator 1.3.1 packaging/install/cluster-operator
fetch external-secrets/external-secrets helm-chart-2.15.0 deploy/crds
fetch opensearch-project/opensearch-k8s-operator opensearch-operator-3.0.14 charts/opensearch-operator/files
fetch cert-manager/cert-manager v1.21.3 deploy/crds
fetch kyverno/kyverno kyverno-chart-3.9.2 config/crds/kyverno
fetch argoproj/argo-cd v3.2.0 manifests/crds

python3 -m pip install -q --disable-pip-version-check pyyaml==6.0.2
curl -sSfL -o "$work/openapi2jsonschema.py" https://raw.githubusercontent.com/yannh/kubeconform/v0.7.0/scripts/openapi2jsonschema.py
cd "$out"
FILENAME_FORMAT='{kind}_{version}' python3 "$work/openapi2jsonschema.py" \
  "$work"/strimzi-kafka-operator/packaging/install/cluster-operator/04*-Crd-*.yaml \
  "$work"/external-secrets/deploy/crds/bundle.yaml \
  "$work"/opensearch-k8s-operator/charts/opensearch-operator/files/opensearch.org_*.yaml \
  "$work"/cert-manager/deploy/crds/cert-manager.io_*.yaml \
  "$work"/kyverno/config/crds/kyverno/kyverno.io_clusterpolicies.yaml \
  "$work"/argo-cd/manifests/crds/application-crd.yaml "$work"/argo-cd/manifests/crds/appproject-crd.yaml >/dev/null
find "$out" -name "*.json" | wc -l
