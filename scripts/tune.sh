#!/usr/bin/env bash
# One tuning candidate (D16): a short run of S1 through a gateway, judged with
# scripts/sustained.py (D15 + D24). Scratch results only; nothing is committed.
#
#   scripts/tune.sh <target> <label> [--profile minimal] [--rate R,R,...] [--duration 30]
#                   [--warmup 10] [--no-verify] [-- VAR=value ...]
#
#   label      names the scratch folder results/scratch-tune-<target>-<label>/ (kept, so
#              scripts/sustained.py can be run on it again; delete with rm -rf when done)
#   --no-verify  skip the target's verify.sh (candidate changes what it checks, e.g. a different server)
#   VAR=value  environment for the candidate (LITELLM_WORKERS=3, BIFROST_GOGC=200, ...),
#              passed through to compose
#
# Prints the sustained table and appends the table to results/scratch-tune-<target>/summary.log.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

target="${1:?target}"
label="${2:?label}"
shift 2
profile=minimal
rate=100,250,500,1000
duration=30
warmup=10
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --rate) rate="$2"; shift 2 ;;
    --duration) duration="$2"; shift 2 ;;
    --no-verify) export GWBENCH_SKIP_VERIFY=1; shift ;;
    --warmup) warmup="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

date_dir="scratch-tune-$target-$label"
rm -rf "results/$date_dir"
mkdir -p "results/scratch-tune-$target"
env "$@" ./scripts/run.sh "$target" s1 --profile "$profile" --rate "$rate" --duration "$duration" \
  --warmup "$warmup" --date "$date_dir" >"results/scratch-tune-$target/$label.out" 2>&1 || {
  tail -n 20 "results/scratch-tune-$target/$label.out" >&2
  echo "run failed" >&2
}
run_dir="results/$date_dir/$target/$profile/s1/run-1"
python3 scripts/sustained.py "$run_dir" | tee -a "results/scratch-tune-$target/summary.log"
