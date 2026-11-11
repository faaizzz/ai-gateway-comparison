#!/usr/bin/env bash
# S6, S7 and S8 runs (D28): fixed 100 RPS, one run per configuration unless --repeat is given.
#
#   scripts/sweep-s6-s8.sh [--date D] [--repeat N] [s6] [s7] [s8]       default: all three
#
# S6 tracing on/off, S7 auth and limits, S8 faults (5 % HTTP 500 and 5 % HTTP 429). The list of
# configurations is below; a configuration that does not exist yet fails its run and is reported,
# the sweep continues. Run with only gwbench containers up (D20).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

date_dir="$(date +%Y-%m-%d)"
repeat=1
parts=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) date_dir="$2"; shift 2 ;;
    --repeat) repeat="$2"; shift 2 ;;
    s6|s7|s8) parts="$parts $1"; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n "$parts" ]] || parts="s6 s7 s8"

# run <target> <profile> <scenario>
run() {
  echo "=== $(date +%H:%M:%S) $3 $1/$2"
  ./scripts/run.sh "$1" "$3" --profile "$2" --repeat "$repeat" --date "$date_dir" || echo "WARNING: $3 $1/$2 had a failed run"
}

for part in $parts; do
  case "$part" in
    s6)
      for c in litellm:prod litellm:prod-langfuse bifrost:minimal bifrost:logs bifrost:prod langfuse:minimal langfuse:prod langfuse:full; do
        run "${c%%:*}" "${c##*:}" s6
      done
      ;;
    s7)
      for c in litellm:minimal litellm:prod litellm:prod-rl bifrost:minimal bifrost:prod bifrost:prod-rl langfuse:minimal langfuse:prod; do
        run "${c%%:*}" "${c##*:}" s7
      done
      ;;
    s8)
      for sc in s8-fail s8-429; do
        run direct none "$sc"
        for c in litellm:retry bifrost:retry langfuse:minimal litellm:minimal bifrost:minimal; do
          run "${c%%:*}" "${c##*:}" "$sc"
        done
      done
      ;;
  esac
done
echo "=== $(date +%H:%M:%S) S6-S8 sweep done"
