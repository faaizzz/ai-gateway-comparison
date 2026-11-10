#!/usr/bin/env bash
# Start or stop the resource sampler (scripts/sampler.py) as a container.
#
#   scripts/sampler.sh start <csv-path-under-results/> <container-glob>...
#   scripts/sampler.sh stop
#
# Example:
#   scripts/sampler.sh start results/s1/run1/resources.csv 'gwbench-mock-llm'
#   ... run the test ...
#   scripts/sampler.sh stop
#
# The CSV path is relative to the repo root and must be under results/ (that
# directory is mounted into the container). The container is named
# gwbench-sampler. `start` returns once the sampler is running; `stop` sends
# SIGTERM, waits for the final flush and removes the container.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

NAME="gwbench-sampler"
COMPOSE=(docker compose -f docker-compose.base.yml)

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

cmd="${1:-}"
case "$cmd" in
  start)
    [[ $# -ge 3 ]] || usage
    out="$2"
    shift 2
    case "$out" in
      results/*) ;;
      *) echo "sampler.sh: CSV path must be under results/ (got: $out)" >&2; exit 2 ;;
    esac
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    mkdir -p "$(dirname "$out")"
    "${COMPOSE[@]}" run -d --name "$NAME" sampler \
      --out "/$out" --exclude "$NAME" "$@" >/dev/null
    # wait for the file with its header row so callers can rely on it existing
    for _ in $(seq 1 50); do
      [[ -s "$out" ]] && exit 0
      sleep 0.1
    done
    echo "sampler.sh: sampler did not start; logs:" >&2
    docker logs "$NAME" >&2 || true
    exit 1
    ;;
  stop)
    if docker inspect "$NAME" >/dev/null 2>&1; then
      docker stop -t 10 "$NAME" >/dev/null
      docker logs "$NAME" 2>&1 | tail -n 1
      docker rm "$NAME" >/dev/null
    else
      echo "sampler.sh: $NAME is not running" >&2
    fi
    ;;
  *) usage ;;
esac
