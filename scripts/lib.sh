#!/usr/bin/env bash
# Shared helpers for scripts/run.sh and scripts/stack.sh. Source it, do not run it.
# Needs bash 3.2 (macOS): no mapfile, no associative arrays, and empty arrays
# are expanded as ${arr[@]+"${arr[@]}"} so `set -u` does not trip.
# The gateway contract these helpers implement is described in gateways/README.md.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_COMPOSE="docker-compose.base.yml"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Names of all targets (directories in gateways/ that have a target.env).
list_targets() {
  local d
  for d in "$REPO_ROOT"/gateways/*/; do
    [[ -f "${d}target.env" ]] && basename "$d"
  done
}

# scenario_script <id>: prints the k6 script (path under k6/) for a scenario id,
# from k6/scenarios/scenarios.txt. Returns 1 if the scenario is unknown.
scenario_script() {
  local id="$1" sid script _rest
  while read -r sid script _rest; do
    case "$sid" in ''|'#'*) continue ;; esac
    if [[ "$sid" == "$id" ]]; then
      [[ -f "$REPO_ROOT/k6/scenarios/$script" ]] || die "scenario $id points at missing k6/scenarios/$script"
      echo "scenarios/$script"
      return 0
    fi
  done <"$REPO_ROOT/k6/scenarios/scenarios.txt"
  return 1
}

list_scenarios() {
  local sid script rest
  while read -r sid script rest; do
    case "$sid" in ''|'#'*) continue ;; esac
    printf '  %-9s %s\n' "$sid" "$rest"
  done <"$REPO_ROOT/k6/scenarios/scenarios.txt"
}

# gw_load <target> [profile]
# Sources gateways/<target>/target.env and sets:
#   TARGET PROFILE TARGET_DIR  COMPOSE_ARGS (array)  COMPOSE_FILES (array, overlays only)
#   TARGET_BASE_URL TARGET_MODEL TARGET_API_KEY TARGET_HEALTH_URL TARGET_CONFIG_FILES ...
gw_load() {
  TARGET="$1"
  local want_profile="${2:-}"
  TARGET_DIR="gateways/$TARGET"
  [[ -f "$REPO_ROOT/$TARGET_DIR/target.env" ]] ||
    die "unknown target '$TARGET' (no $TARGET_DIR/target.env). Targets: $(list_targets | tr '\n' ' ')"

  # Start from a clean slate so one target's settings never leak into another.
  unset TARGET_DESCRIPTION TARGET_PROFILES TARGET_COMPOSE_FILES TARGET_BASE_URL TARGET_MODEL \
    TARGET_API_KEY TARGET_HEALTH_URL TARGET_CONFIG_FILES TARGET_SMOKE_TIMEOUT_S TARGET_CONTAINERS \
    TARGET_POST_RUN TARGET_POST_RUN_FOR

  # Profile is chosen before sourcing so target.env can use $PROFILE (for example
  # to pick docker-compose.$PROFILE.yml). The list of valid profiles is only known
  # after sourcing, so first read it in a throwaway shell.
  local valid
  valid="$(cd "$REPO_ROOT" && PROFILE="" bash -c '. "$1"; echo "${TARGET_PROFILES:-minimal prod}"' _ "$TARGET_DIR/target.env")"
  if [[ -z "$want_profile" ]]; then
    want_profile="${valid%% *}"
  fi
  local ok=0 p
  for p in $valid; do [[ "$p" == "$want_profile" ]] && ok=1; done
  [[ $ok -eq 1 ]] || die "target '$TARGET' has no profile '$want_profile' (valid: $valid)"
  PROFILE="$want_profile"

  # shellcheck source=/dev/null
  . "$REPO_ROOT/$TARGET_DIR/target.env"

  : "${TARGET_BASE_URL:?$TARGET_DIR/target.env must set TARGET_BASE_URL}"
  : "${TARGET_MODEL:?$TARGET_DIR/target.env must set TARGET_MODEL}"
  : "${TARGET_API_KEY:?$TARGET_DIR/target.env must set TARGET_API_KEY}"
  TARGET_HEALTH_URL="${TARGET_HEALTH_URL:-}"
  TARGET_CONFIG_FILES="${TARGET_CONFIG_FILES:-}"
  TARGET_SMOKE_TIMEOUT_S="${TARGET_SMOKE_TIMEOUT_S:-120}"
  TARGET_COMPOSE_FILES="${TARGET_COMPOSE_FILES:-}"

  COMPOSE_FILES=()
  local f
  for f in $TARGET_COMPOSE_FILES; do
    [[ -f "$REPO_ROOT/$f" ]] || die "$TARGET_DIR/target.env: compose file $f does not exist"
    COMPOSE_FILES+=("$f")
  done
  COMPOSE_ARGS=(-f "$BASE_COMPOSE")
  for f in ${COMPOSE_FILES[@]+"${COMPOSE_FILES[@]}"}; do COMPOSE_ARGS+=(-f "$f"); done

  # Visible to compose interpolation in overlays.
  export GWBENCH_TARGET="$TARGET" GWBENCH_PROFILE="$PROFILE"
  # k6 runs as the caller, so it can write results/ on Linux hosts (see the k6 service).
  GWBENCH_UID="$(id -u)"
  GWBENCH_GID="$(id -g)"
  export GWBENCH_UID GWBENCH_GID
}

compose() { (cd "$REPO_ROOT" && docker compose "${COMPOSE_ARGS[@]}" "$@"); }

# gw_up: base stack (mock) plus the target's overlay, waits for healthchecks.
# --remove-orphans removes any other gateway stack (contract: one at a time).
gw_up() {
  log "starting stack: target=$TARGET profile=$PROFILE"
  compose up -d --wait --remove-orphans || {
    compose ps -a >&2 || true
    die "stack did not become healthy"
  }
}

# gw_down: removes the mock and the target stack, with volumes. The optional
# obs profile (Prometheus, Grafana) is not touched.
gw_down() {
  log "tearing down stack"
  compose down -v --remove-orphans >/dev/null 2>&1 || compose down -v --remove-orphans || true
}

# gw_faults VAR=value ...: recreates the mock with fault knobs (S8), after the smoke and verify
# steps ran against a healthy upstream. The mock's fault sequence restarts with the container.
gw_faults() {
  log "recreating the mock with faults: $*"
  (cd "$REPO_ROOT" && env "$@" docker compose "${COMPOSE_ARGS[@]}" up -d --no-deps --force-recreate --wait mock-llm) ||
    die "could not recreate the mock with $*"
}

# k6_run <k6 args...>: one-off k6 container on the gwbench network.
k6_run() { compose run --rm -T --no-deps k6 run "$@"; }

# k6_target_env: prints "-e KEY=VALUE" pairs, one per line, for the target.
k6_target_env() {
  printf '%s\n' \
    -e "TARGET_URL=$TARGET_BASE_URL" -e "MODEL=$TARGET_MODEL" -e "API_KEY=$TARGET_API_KEY" \
    -e "HEALTH_URL=$TARGET_HEALTH_URL" \
    -e "BENCH_TARGET=$TARGET" -e "BENCH_PROFILE=$PROFILE" \
    -e "EXPECT_CHUNKS=${MOCK_OUTPUT_TOKENS:-40}"
}

# gw_smoke: runs k6/smoke_target.js until it passes or TARGET_SMOKE_TIMEOUT_S
# runs out (gateways need a moment after their healthcheck goes green).
gw_smoke() {
  local deadline out args=() line
  deadline=$(($(date +%s) + TARGET_SMOKE_TIMEOUT_S))
  while IFS= read -r line; do args+=("$line"); done < <(k6_target_env)
  while :; do
    if out="$(k6_run "${args[@]}" /k6/smoke_target.js 2>&1)"; then
      log "smoke request passed"
      return 0
    fi
    if [[ $(date +%s) -ge $deadline ]]; then
      printf '%s\n' "$out" | tail -n 25 >&2
      die "smoke request still failing after ${TARGET_SMOKE_TIMEOUT_S}s"
    fi
    sleep 2
  done
}

# gw_verify: optional gateways/<target>/verify.sh checks that the profile's mode
# is really active (contract rule 6). It runs on the host after the smoke test.
gw_verify() {
  # Only for tuning candidates whose process layout verify.sh does not know (scripts/tune.sh
  # --no-verify). Never set it for a measured run.
  if [[ "${GWBENCH_SKIP_VERIFY:-0}" == 1 ]]; then log "verify.sh SKIPPED (GWBENCH_SKIP_VERIFY=1, tuning candidate)"; return 0; fi
  local hook="$REPO_ROOT/$TARGET_DIR/verify.sh"
  [[ -f "$hook" ]] || { log "no verify.sh for $TARGET (nothing to check)"; return 0; }
  log "verifying mode with $TARGET_DIR/verify.sh"
  (cd "$REPO_ROOT" && TARGET="$TARGET" PROFILE="$PROFILE" bash "$hook") || die "verify.sh failed: mode is not what the profile claims"
}
