#!/usr/bin/env bash
# Repo checks, same locally (`make lint`) and in CI.
#   1. docker compose config -q for every compose file found
#   2. shellcheck on scripts/*.sh
#   3. ruff on Python files
# Needs only Docker and bash (3.2 on macOS is fine). Linters run in containers; no host installs.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SHELLCHECK_IMAGE="${SHELLCHECK_IMAGE:-koalaman/shellcheck-alpine:v0.10.0}"
RUFF_IMAGE="${RUFF_IMAGE:-ghcr.io/astral-sh/ruff:0.8.6}"
BASE="docker-compose.base.yml"
fail=0

echo "== compose config"
# Every compose file is validated. Files other than the base are overlays, so
# they are validated merged on top of the base file.
compose_files=()
while IFS= read -r f; do compose_files+=("$f"); done < <(
  find . \( -path ./.git -o -path ./node_modules -o -path ./results \) -prune -o \
    -type f \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' \
      -o -name 'compose*.yml' -o -name 'compose*.yaml' \) -print | sort
)
for f in "${compose_files[@]}"; do
  f="${f#./}"
  if [[ "$f" == "$BASE" ]]; then
    args=(-f "$f")
  else
    args=(-f "$BASE" -f "$f")
  fi
  if docker compose "${args[@]}" config -q; then
    echo "ok   $f"
  else
    echo "FAIL $f"
    fail=1
  fi
done
[[ ${#compose_files[@]} -gt 0 ]] || echo "no compose files found"

echo "== shellcheck"
sh_files=()
while IFS= read -r f; do sh_files+=("$f"); done < <(find scripts -maxdepth 1 -type f -name '*.sh' | sort)
if [[ ${#sh_files[@]} -gt 0 ]]; then
  if docker run --rm -v "$PWD":/w -w /w "$SHELLCHECK_IMAGE" shellcheck "${sh_files[@]}"; then
    echo "ok   ${#sh_files[@]} script(s)"
  else
    fail=1
  fi
else
  echo "no shell scripts found"
fi

echo "== ruff"
py_files=()
while IFS= read -r f; do py_files+=("$f"); done < <(
  find . \( -path ./.git -o -path ./node_modules -o -path ./results -o -name target -o -name __pycache__ \) -prune -o \
    -type f -name '*.py' -print | sort
)
if [[ ${#py_files[@]} -gt 0 ]]; then
  if docker run --rm -v "$PWD":/w -w /w "$RUFF_IMAGE" check .; then
    echo "ok   ${#py_files[@]} file(s)"
  else
    fail=1
  fi
else
  echo "no python files found"
fi

exit "$fail"
