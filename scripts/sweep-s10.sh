#!/usr/bin/env bash
# S10 max-rate sweep (D17, D24, D26, D28): per gateway and profile, the slice of the extended
# ladder around its limit, 30 s steps after a 10 s warm-up, 3 repeats on fresh stacks.
#
#   scripts/sweep-s10.sh [--date D] [--profile minimal|prod] [target ...]
#
# Judge the result with scripts/sustained.py results/<date>/<target>/<profile>/s10/run-*.
# Run with only gwbench containers up (D20).
# (redirect it yourself; the summaries are the record).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

date_dir="$(date +%Y-%m-%d)"
profile=minimal
targets=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) date_dir="$2"; shift 2 ;;
    --profile) profile="$2"; shift 2 ;;
    *) targets="$targets $1"; shift ;;
  esac
done
[[ -n "$targets" ]] || targets="litellm langfuse bifrost"

ladder() { # target profile -> rates slice (each ends one or two steps above the expected limit)
  case "$1/$2" in
    litellm/*) echo 100,200,250,300,350,400,500 ;;
    langfuse/minimal) echo 250,500,750,1000,1250,1500 ;;
    langfuse/prod) echo 100,250,500,750,1000,1250 ;;
    bifrost/*) echo 1000,1500,2000,3000,4000,6000,8000,10000,12000,16000 ;;
    *) echo "no ladder for $1/$2" >&2; return 1 ;;
  esac
}

for t in $targets; do
  rates="$(ladder "$t" "$profile")"
  echo "=== $(date +%H:%M:%S) S10 $t/$profile rates $rates"
  ./scripts/run.sh "$t" s10 --profile "$profile" --rate "$rates" --duration 30 --warmup 10 \
    --repeat 3 --date "$date_dir" || echo "WARNING: $t/$profile had a failed run"
done
echo "=== $(date +%H:%M:%S) S10 sweep done"
