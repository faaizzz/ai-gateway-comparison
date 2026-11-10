#!/usr/bin/env bash
# Bring a target's stack up or down by hand (for debugging and for `make up`,
# `make down`, `make smoke`). The full benchmark flow is scripts/run.sh.
#
#   scripts/stack.sh up    <target> [--profile P]   start mock + target, wait until healthy
#   scripts/stack.sh smoke <target> [--profile P]   send the smoke request (and the verify hook)
#   scripts/stack.sh down  [<target>] [--profile P] remove the mock and the target stack, with volumes
#
# Without a target, `down` removes everything the base compose project started
# (the mock, and any gateway stack as an orphan), but not the optional obs profile.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/lib.sh
. scripts/lib.sh

usage() {
  sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

cmd="${1:-}"
[[ -n "$cmd" ]] || usage
shift
target=""
profile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="${2:?--profile needs a value}"; shift 2 ;;
    -h|--help) usage ;;
    -*) die "unknown flag $1" ;;
    *) [[ -z "$target" ]] || die "unexpected argument $1"; target="$1"; shift ;;
  esac
done

case "$cmd" in
  up)
    [[ -n "$target" ]] || usage
    gw_load "$target" "$profile"
    gw_up
    ;;
  smoke)
    [[ -n "$target" ]] || usage
    gw_load "$target" "$profile"
    gw_smoke
    gw_verify
    ;;
  down)
    gw_load "${target:-direct}" "$profile"
    gw_down
    ;;
  *) usage ;;
esac
