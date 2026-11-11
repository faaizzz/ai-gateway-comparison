#!/usr/bin/env bash
# Run one scenario against one target and store reproducible results.
#
#   scripts/run.sh <target> <scenario> [--profile P] [--repeat N] [--duration S]
#                  [--rate R[,R...]] [--warmup S] [--raw] [--keep] [--date D]
#
#   target      a directory in gateways/ with a target.env (direct, litellm, bifrost, langfuse)
#   scenario    an id from k6/scenarios/scenarios.txt (s1, s1-ramp, ...)
#   --profile   minimal | prod (the targets' own list; "direct" only has "none"). Default: the target's first
#   --repeat    number of independent runs, each on a fresh stack. Default 1
#   --duration  seconds per measured step (scenario default: 60, s1-ramp 15)
#   --rate      one rate or a comma list of requests per second, replacing the scenario's ladder
#   --warmup    warm-up seconds before the steps, excluded from results (scenario default: 15, s1-ramp 10)
#   --raw       also write k6's per-request JSON to raw/ (gitignored; slows k6 down at high rates)
#   --keep      leave the stack running afterwards (debugging)
#   --date D    results date directory, default today (YYYY-MM-DD)
#
# Per run: start mock + target stack, wait until healthy, smoke request, verify
# hook, then sampler + k6 (warm-up excluded), then metadata, then tear down.
# Fault scenarios (s8-fail, s8-429): after verify.sh the mock is recreated with the fault knob
# (MOCK_FAIL_RATE=0.05 or MOCK_429_RATE=0.05, override with S8_FAIL_RATE / S8_429_RATE), so the
# verify checks run against a healthy upstream. Targets can set TARGET_POST_RUN (a command that
# gets the run directory) and TARGET_POST_RUN_FOR (scenario ids) in target.env, e.g. the S6
# trace count; it runs after k6, before the metadata.
# Output: results/<date>/<target>/<profile>/<scenario>/run-<n>/
#   summary.json   k6 summary export (read per-step sub-metrics, see k6/lib/arrival.js)
#   resources.csv  per-container CPU/memory, 1 s
#   metadata.json  git SHA, images and digests, mock knobs, host and Docker resources, timing
#   k6.log, compose.resolved.yml, config/   console output and the config snapshot
#
# Mock knobs: set MOCK_* in the environment (see mock-llm/README.md). Exit code is
# non-zero if any run failed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/lib.sh
. scripts/lib.sh

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  printf '\nTargets:\n' >&2
  list_targets | sed 's/^/  /' >&2
  printf '\nScenarios:\n' >&2
  list_scenarios >&2
  exit 2
}

target=""
scenario=""
profile=""
repeat=1
duration=""
rate=""
warmup=""
raw=0
keep=0
date_dir="$(date +%Y-%m-%d)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="${2:?--profile needs a value}"; shift 2 ;;
    --repeat) repeat="${2:?--repeat needs a value}"; shift 2 ;;
    --duration) duration="${2:?--duration needs a value}"; shift 2 ;;
    --rate) rate="${2:?--rate needs a value}"; shift 2 ;;
    --warmup) warmup="${2:?--warmup needs a value}"; shift 2 ;;
    --date) date_dir="${2:?--date needs a value}"; shift 2 ;;
    --raw) raw=1; shift ;;
    --keep) keep=1; shift ;;
    -h|--help) usage ;;
    -*) die "unknown flag $1 (see --help)" ;;
    *)
      if [[ -z "$target" ]]; then target="$1"
      elif [[ -z "$scenario" ]]; then scenario="$1"
      else die "unexpected argument $1"
      fi
      shift
      ;;
  esac
done
[[ -n "$target" && -n "$scenario" ]] || usage

[[ "$repeat" =~ ^[0-9]+$ && "$repeat" -ge 1 ]] || die "--repeat must be a positive integer"
[[ -z "$duration" || "$duration" =~ ^[0-9]+$ ]] || die "--duration is whole seconds"
[[ -z "$warmup" || "$warmup" =~ ^[0-9]+$ ]] || die "--warmup is whole seconds"
[[ -z "$rate" || "$rate" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "--rate is a number or a comma list of numbers"

script="$(scenario_script "$scenario")" || die "unknown scenario '$scenario'. Scenarios:
$(list_scenarios)"
gw_load "$target" "$profile"

base_dir="results/$date_dir/$target/$PROFILE/$scenario"
failed=0

faults=""
case "$scenario" in
  s8-fail) faults="MOCK_FAIL_RATE=${S8_FAIL_RATE:-0.05}" ;;
  s8-429) faults="MOCK_429_RATE=${S8_429_RATE:-0.05}" ;;
esac

cleanup() {
  ./scripts/sampler.sh stop >/dev/null 2>&1 || true
  if [[ $keep -eq 0 ]]; then gw_down; else log "--keep: stack left running (scripts/stack.sh down to remove)"; fi
}
trap cleanup EXIT

next_run_number() {
  local n=1
  while [[ -e "$base_dir/run-$n" ]]; do n=$((n + 1)); done
  echo "$n"
}

run_once() {
  local n run_dir t_start t_end k6_exit=0 line args=()
  n="$(next_run_number)"
  run_dir="$base_dir/run-$n"
  mkdir -p "$run_dir/raw"
  log "=== $target / $PROFILE / $scenario / run-$n  ->  $run_dir"

  gw_up
  gw_smoke
  gw_verify
  [[ -z "$faults" ]] || gw_faults "$faults"

  # k6 arguments: target settings first, then scenario flags, then outputs.
  while IFS= read -r line; do args+=("$line"); done < <(k6_target_env)
  args+=(-e "BENCH_SCENARIO=$scenario")
  [[ -z "$rate" ]] || args+=(-e "RATES=$rate")
  [[ -z "$duration" ]] || args+=(-e "STEP_S=$duration")
  [[ -z "$warmup" ]] || args+=(-e "WARMUP_S=$warmup")
  # --quiet: no per-second progress lines in k6.log
  args+=(--quiet --summary-export "/$run_dir/summary.json")
  [[ $raw -eq 0 ]] || args+=(--out "json=/$run_dir/raw/requests.json")

  ./scripts/sampler.sh start "$run_dir/resources.csv" "${TARGET_CONTAINERS:-gwbench-*}"
  t_start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "k6: $script"
  set +e
  k6_run "${args[@]}" "/k6/$script" 2>&1 | tee "$run_dir/k6.log"
  k6_exit="${PIPESTATUS[0]}"
  set -e
  t_end="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ./scripts/sampler.sh stop >/dev/null 2>&1 || true

  if [[ -n "${TARGET_POST_RUN:-}" && "$k6_exit" -eq 0 && " ${TARGET_POST_RUN_FOR:-} " == *" $scenario "* ]]; then
    local hook=()
    read -r -a hook <<<"$TARGET_POST_RUN"
    log "post-run hook: $TARGET_POST_RUN"
    "${hook[@]}" "$run_dir" || { log "post-run hook failed"; failed=1; }
  fi

  # metadata is written while the stack is still up (container limits, mock knobs).
  python3 scripts/metadata.py \
    --run-dir "$run_dir" --target "$target" --profile "$PROFILE" --scenario "$scenario" \
    --run "$n" --started "$t_start" --finished "$t_end" --k6-exit "$k6_exit" \
    --k6-script "$script" --k6-log "$run_dir/k6.log" \
    --compose-files "$BASE_COMPOSE ${TARGET_COMPOSE_FILES}" --config-files "${TARGET_CONFIG_FILES}" \
    --base-url "$TARGET_BASE_URL" --model "$TARGET_MODEL" \
    --flags "repeat=$repeat duration=${duration:-default} rate=${rate:-default} warmup=${warmup:-default} raw=$raw"

  if [[ "$k6_exit" -ne 0 ]]; then
    log "k6 exited with $k6_exit"
    failed=1
  fi
  [[ -s "$run_dir/summary.json" ]] || { log "no summary.json written"; failed=1; }
  python3 scripts/summarize.py "$run_dir" || true
  gw_down
}

for _ in $(seq 1 "$repeat"); do
  run_once
done

[[ $failed -eq 0 ]] || die "at least one run failed"
log "done: $base_dir"
