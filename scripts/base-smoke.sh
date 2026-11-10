#!/usr/bin/env bash
# End-to-end check of the base stack: mock healthy, custom k6 image runs a plain
# and an SSE request against it, and the sampler writes a CSV for the mock.
#
#   scripts/base-smoke.sh            (or: make base-smoke)
#
# Leaves the mock running (stop it with `make base-down`). Exit code 0 = pass.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE=(docker compose -f docker-compose.base.yml)
OUT="results/base-smoke/resources.csv"

"${COMPOSE[@]}" up -d --wait mock-llm
status="$(docker inspect -f '{{.State.Health.Status}}' gwbench-mock-llm)"
echo "mock-llm health: $status"
[[ "$status" == "healthy" ]] || exit 1

rm -f "$OUT"
./scripts/sampler.sh start "$OUT" gwbench-mock-llm
trap './scripts/sampler.sh stop >/dev/null 2>&1 || true' EXIT

"${COMPOSE[@]}" run --rm k6 run -e DURATION="${DURATION:-8s}" /k6/smoke.js

./scripts/sampler.sh stop
trap - EXIT

rows="$(($(wc -l <"$OUT") - 1))"
echo "sampler rows for gwbench-mock-llm: $rows"
head -n 3 "$OUT"
[[ "$rows" -ge 5 ]] || { echo "too few sampler rows" >&2; exit 1; }
awk -F, 'NR>1 && $2!="gwbench-mock-llm"{bad=1} END{exit bad}' "$OUT"
echo "base smoke: PASS"
