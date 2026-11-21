#!/usr/bin/env bash
# Run a check in every OpenTofu root under infra/modules and infra/stacks.
#   infra/bin/each.sh validate   init without a backend, then validate
#   infra/bin/each.sh test       tofu test, against mocked providers, where a module has tests/
#   infra/bin/each.sh lock       write .terraform.lock.hcl in every stack for Linux and macOS (needs the registry)
set -euo pipefail
cd "$(dirname "$0")/.."
TOFU="${TOFU:-tofu}"
what="${1:?validate, test or lock}"
case "$what" in
  validate|test) roots=(modules/*/* stacks/*/*) ;;
  lock) roots=(stacks/*/*) ;;
  *) echo "unknown check: $what" >&2; exit 2 ;;
esac
mkdir -p .tofu/check
failed=()
for dir in "${roots[@]}"; do
  compgen -G "$dir/*.tf" > /dev/null || continue
  if [ "$what" = test ] && [ ! -d "$dir/tests" ]; then continue; fi
  echo "::group::$what $dir"
  export TF_DATA_DIR="$PWD/.tofu/check/${dir//\//-}"
  if [ "$what" = lock ]; then
    run=("$TOFU" -chdir="$dir" providers lock -platform=linux_amd64 -platform=linux_arm64 -platform=darwin_arm64 -platform=darwin_amd64)
  else
    run=("$TOFU" -chdir="$dir" "$what" -no-color)
  fi
  if "$TOFU" -chdir="$dir" init -backend=false -input=false -no-color >"$TF_DATA_DIR.init.log" 2>&1 && "${run[@]}"; then
    echo "ok   $dir"
  else
    [ -f "$TF_DATA_DIR.init.log" ] && tail -20 "$TF_DATA_DIR.init.log"
    echo "FAIL $dir"; failed+=("$dir")
  fi
  echo "::endgroup::"
done
if [ ${#failed[@]} -gt 0 ]; then
  printf 'failed: %s\n' "${failed[@]}" >&2
  exit 1
fi
