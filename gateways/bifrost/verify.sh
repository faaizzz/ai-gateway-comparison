#!/usr/bin/env bash
# Checks that the Bifrost stack is the profile it claims to be (contract rule 6).
# Run by scripts/run.sh and scripts/smoke.sh after the smoke request, from the repo root,
# with TARGET and PROFILE in the environment. Exit non-zero on any mismatch.
# The checks are in verify.py (stdlib Python on the host; the Bifrost image has no curl
# and its busybox wget hides error bodies): see its header for the list, in short
#   minimal   config store off AND log store off: no database file ever appears in the
#             data dir, no logs API, no logging or telemetry plugin
#   prod      config store and log store on: both SQLite files, the burst shows up in the
#             logs API, governance charges the virtual key, /metrics answers
set -euo pipefail
exec python3 -I "$(dirname "${BASH_SOURCE[0]}")/verify.py"
