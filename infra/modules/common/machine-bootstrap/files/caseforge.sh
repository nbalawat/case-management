#!/usr/bin/env bash
# The cloud machine's own control command. Cloud-init installs it as /usr/local/bin/caseforge and runs
# `caseforge provision` once; after that, from a laptop:  ssh caseforge caseforge status
#
#   caseforge status            service state, containers, whether the app answers, when the machine stops itself
#   caseforge wait [MINUTES]    block until the app answers /readyz (default 30 minutes)
#   caseforge logs [SERVICE]    follow the stack's logs (all services, or one: app, kafka, keycloak, ...)
#   caseforge restart           restart the stack (data is kept)
#   caseforge stack NAME        switch the Makefile target that runs (full, sso, observability, streaming, ...)
#   caseforge update [REF]      fetch REF (default: the configured one) from Git and restart on it
#   caseforge env KEY=VALUE     set a value in the stack's .env (for example ANTHROPIC_API_KEY); restart to apply
#   caseforge extend [HOURS]    push the automatic stop back to HOURS from now (default: the configured hours)
#   caseforge reset --yes       delete every container and volume (all data), then start again from a fresh seed
#
# Settings live in /etc/caseforge/machine.env (written by cloud-init from the OpenTofu variables).
set -euo pipefail

CONF="${CASEFORGE_CONF:-/etc/caseforge/machine.env}"
# shellcheck source=/dev/null
[ -f "$CONF" ] && . "$CONF"
SRC="${CASEFORGE_SRC:-/opt/caseforge/src}"
STACK="${STACK:-full}"
GIT_URL="${GIT_URL:-https://github.com/nbalawat/case-management.git}"
GIT_REF="${GIT_REF:-main}"
AUTO_STOP_HOURS="${AUTO_STOP_HOURS:-10}"
OWNER="${OWNER:-caseforge}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
SHUTDOWN="${SHUTDOWN:-shutdown}"

say() { printf '%s\n' "$*"; }
die() { printf 'caseforge: %s\n' "$*" >&2; exit 1; }
# Git as the checkout's owner: root may not read a repository another user owns ("dubious ownership").
git_as_owner() { if [ "$(id -un)" = "$OWNER" ]; then git "$@"; else sudo -u "$OWNER" git "$@"; fi; }
# Re-run as root through sudo once; CASEFORGE_ESCALATED stops a loop if sudo does not make us root.
# CASEFORGE_SKIP_ROOT=1 runs as the current user (the unit tests, with systemctl and shutdown stubbed).
need_root() {
  [ "$(id -u)" -eq 0 ] || [ "${CASEFORGE_SKIP_ROOT:-0}" = 1 ] && return 0
  [ "${CASEFORGE_ESCALATED:-0}" = 1 ] && die "needs root, and sudo did not provide it"
  exec sudo -E CASEFORGE_ESCALATED=1 "$0" "$@"
}

set_conf() {  # set_conf KEY VALUE: replace or append KEY=VALUE in the settings file
  local key="$1" value="$2"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "bad setting name: $key"
  if grep -q "^${key}=" "$CONF" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$CONF"
  else
    printf '%s=%s\n' "$key" "$value" >> "$CONF"
  fi
}

install_docker() {
  command -v docker >/dev/null && docker compose version >/dev/null 2>&1 && return 0
  # Docker's own apt repository: Ubuntu's docker.io lags behind on Compose features the stack uses.
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  . /etc/os-release
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $SYSTEMCTL enable --now docker
}

fetch_source() {
  local ref="${1:-$GIT_REF}"
  if [ ! -d "$SRC/.git" ]; then
    install -d -o "$OWNER" -g "$OWNER" "$(dirname "$SRC")"
    git_as_owner clone --quiet "$GIT_URL" "$SRC"
  fi
  git_as_owner -C "$SRC" fetch --quiet --prune origin "+refs/heads/*:refs/remotes/origin/*" "+refs/tags/*:refs/tags/*"
  # A branch name follows its remote; a tag or commit is checked out as is.
  if git_as_owner -C "$SRC" rev-parse --verify --quiet "refs/remotes/origin/$ref" >/dev/null; then
    git_as_owner -C "$SRC" checkout --quiet -B "$ref" "origin/$ref"
  else
    git_as_owner -C "$SRC" checkout --quiet --detach "$ref"
  fi
  say "source: $ref at $(git_as_owner -C "$SRC" rev-parse --short HEAD)"
}

schedule_stop() {  # schedule_stop HOURS: 0 turns the automatic stop off
  local hours="${1:-$AUTO_STOP_HOURS}"
  [[ "$hours" =~ ^[0-9]+$ ]] || die "hours must be a whole number: $hours"
  $SHUTDOWN -c 2>/dev/null || true
  if [ "$hours" -gt 0 ]; then
    $SHUTDOWN -h "+$((hours * 60))" "CaseForge stops itself after ${hours}h; run 'caseforge extend' to keep it on" >/dev/null 2>&1
    say "the machine stops itself in ${hours}h (disk and data are kept)"
  else
    say "automatic stop is off: the machine runs until you stop it"
  fi
}

stop_time() {
  local f=/run/systemd/shutdown/scheduled usec
  [ -f "$f" ] || { say "not scheduled"; return; }
  usec="$(sed -n 's/^USEC=//p' "$f")"
  date -d "@$((usec / 1000000))" '+%Y-%m-%d %H:%M %Z'
}

ready() { curl -fsS -m 5 -o /dev/null http://localhost:8000/readyz; }

cmd_provision() {
  need_root provision "$@"
  install_docker
  usermod -aG docker "$OWNER"
  fetch_source
  install -d -o "$OWNER" -g "$OWNER" "$SRC/data"
  $SYSTEMCTL daemon-reload
  $SYSTEMCTL enable caseforge.service caseforge-autostop.service
  $SYSTEMCTL start caseforge-autostop.service
  $SYSTEMCTL start --no-block caseforge.service
  say "provisioned: stack '$STACK' is starting (first start builds images and seeds the demo, about 10-20 minutes)"
}

cmd_run() {  # the service's ExecStart: the same Makefile target a laptop runs, in the foreground
  cd "$SRC"
  exec make "$STACK"
}

cmd_halt() {  # the service's ExecStop: stop every container of every profile, keep the volumes
  cd "$SRC"
  docker compose --profile '*' stop --timeout 60
}

cmd_status() {
  say "stack:      $STACK (ref $GIT_REF, commit $(git_as_owner -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo none))"
  say "service:    $($SYSTEMCTL is-active caseforge.service 2>/dev/null || true)"
  if ready 2>/dev/null; then say "app:        ready on http://localhost:8000"; else say "app:        not answering yet"; fi
  say "auto stop:  $(stop_time)"
  say "disk:       $(df -h / | awk 'NR==2 {print $3 " used of " $2}')"
  say "memory:     $(free -g | awk '/^Mem:/ {print $3 " GB used of " $2 " GB"}')"
  say ""
  (cd "$SRC" && docker compose --profile '*' ps --format 'table {{.Service}}\t{{.Status}}') 2>/dev/null || true
}

cmd_wait() {
  local minutes="${1:-30}" deadline
  deadline=$(( $(date +%s) + minutes * 60 ))
  until ready 2>/dev/null; do
    [ "$(date +%s)" -lt "$deadline" ] || die "the app did not answer within ${minutes} minutes; see: caseforge logs app"
    sleep 10
  done
  say "ready"
}

cmd_logs() { cd "$SRC" && exec docker compose --profile '*' logs --follow --tail 200 "$@"; }

cmd_restart() { need_root restart "$@"; $SYSTEMCTL restart caseforge.service; say "restarting stack '$STACK'"; }

cmd_stack() {
  need_root stack "$@"
  local name="${1:?usage: caseforge stack NAME}"
  grep -qE "^${name}:" "$SRC/Makefile" || die "no Makefile target called '$name'"
  set_conf STACK "$name"
  $SYSTEMCTL restart caseforge.service
  say "stack is now '$name'"
}

cmd_update() {
  need_root update "$@"
  local ref="${1:-$GIT_REF}"
  fetch_source "$ref"
  set_conf GIT_REF "$ref"
  $SYSTEMCTL restart caseforge.service
}

cmd_env() {
  local pair="${1:?usage: caseforge env KEY=VALUE}" key
  key="${pair%%=*}"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] && [[ "$pair" == *=* ]] || die "expected KEY=VALUE"
  touch "$SRC/.env" && chmod 600 "$SRC/.env"
  if grep -q "^${key}=" "$SRC/.env"; then
    grep -v "^${key}=" "$SRC/.env" > "$SRC/.env.tmp"; mv "$SRC/.env.tmp" "$SRC/.env"; chmod 600 "$SRC/.env"
  fi
  printf '%s\n' "$pair" >> "$SRC/.env"
  say "set $key in the stack's .env; run 'caseforge restart' to apply"
}

cmd_extend() { need_root extend "$@"; schedule_stop "${1:-$AUTO_STOP_HOURS}"; }

cmd_reset() {
  [ "${1:-}" = "--yes" ] || die "this deletes every container and volume (all data); run: caseforge reset --yes"
  need_root reset "$@"
  $SYSTEMCTL stop caseforge.service
  (cd "$SRC" && docker compose --profile '*' down --volumes --remove-orphans)
  $SYSTEMCTL start caseforge.service
  say "reset: the stack starts again from a fresh seed"
}

main() {
  local cmd="${1:-status}"
  shift || true
  case "$cmd" in
    provision|run|halt|status|wait|logs|restart|stack|update|env|extend|reset) "cmd_$cmd" "$@" ;;
    autostop) need_root autostop "$@"; schedule_stop "$AUTO_STOP_HOURS" ;;
    help|-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' ;;
    *) die "unknown command '$cmd' (try: caseforge help)" ;;
  esac
}

[ "${CASEFORGE_SOURCE_ONLY:-0}" = 1 ] || main "$@"
