#!/usr/bin/env bash
# S9 soak sweep (D28): 30 minutes at 100 RPS per target, minimal profile, as six 300 s windows.
#
#   scripts/sweep-s9.sh [--date D] [target ...]      default targets: litellm bifrost langfuse direct
#
# Run with only gwbench containers up (D20). About 35 minutes per target.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

date_dir="$(date +%Y-%m-%d)"
targets=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) date_dir="$2"; shift 2 ;;
    *) targets="$targets $1"; shift ;;
  esac
done
[[ -n "$targets" ]] || targets="litellm bifrost langfuse direct"

for t in $targets; do
  profile=minimal
  [[ "$t" == direct ]] && profile=none
  echo "=== $(date +%H:%M:%S) S9 $t/$profile"
  ./scripts/run.sh "$t" s9 --profile "$profile" --rate 100 --duration 300 --warmup 15 --date "$date_dir" ||
    echo "WARNING: $t/$profile had a failed run"
done
echo "=== $(date +%H:%M:%S) S9 sweep done"
