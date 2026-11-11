#!/usr/bin/env bash
# Smoke test for one gateway target: a non-streaming and a streaming chat
# completion through the gateway to the mock, then the target's verify.sh.
# Fails (non-zero exit) on any error.
#
#   scripts/smoke.sh <target> [profile] [--keep | --no-up]
#
#   profile   minimal | prod (the target's own list, default: its first)
#   --keep    leave the stack running afterwards
#   --no-up   the stack is already up (make up): only run the checks, never start or stop it
#
# Without a flag: start mock + target stack, wait until healthy, check, tear the
# stack down with its volumes. Example: scripts/smoke.sh litellm prod
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/lib.sh
. scripts/lib.sh

usage() {
  sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  printf '\nTargets:\n' >&2
  list_targets | sed 's/^/  /' >&2
  exit 2
}

target=""
profile=""
keep=0
no_up=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) keep=1; shift ;;
    --no-up) no_up=1; shift ;;
    -h|--help) usage ;;
    -*) die "unknown flag $1" ;;
    *)
      if [[ -z "$target" ]]; then target="$1"
      elif [[ -z "$profile" ]]; then profile="$1"
      else die "unexpected argument $1"
      fi
      shift
      ;;
  esac
done
[[ -n "$target" ]] || usage
gw_load "$target" "$profile"

if [[ $no_up -eq 0 ]]; then
  trap '[[ $keep -eq 1 ]] || gw_down' EXIT
  gw_up
fi

# Non-streaming, with retries while the gateway warms up (k6/smoke_target.js).
gw_smoke

# Streaming (k6/smoke_stream.js), same retry window.
args=()
while IFS= read -r line; do args+=("$line"); done < <(k6_target_env)
deadline=$(($(date +%s) + TARGET_SMOKE_TIMEOUT_S))
while :; do
  if out="$(k6_run "${args[@]}" /k6/smoke_stream.js 2>&1)"; then
    log "streaming smoke request passed"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    printf '%s\n' "$out" | tail -n 25 >&2
    die "streaming smoke request still failing after ${TARGET_SMOKE_TIMEOUT_S}s"
  fi
  sleep 2
done

gw_verify
log "smoke $TARGET/$PROFILE: PASS"
