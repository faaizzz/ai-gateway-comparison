#!/usr/bin/env bash
# Checks that the Langfuse stack is the profile it claims to be (contract rule 6).
# Run by scripts/run.sh and scripts/smoke.sh after the smoke request, from the repo root,
# with TARGET and PROFILE in the environment. Exit non-zero on any mismatch.
# The checks are in verify.py (stdlib Python on the host; the gateway image has no python):
# see its header. In short
#   both     stack up, gateway limit and image, allow-list on gateway and Web, ingestion mode
#            in Postgres, connection to the mock, bad key refused, request answered by the mock
#   minimal  ingestion mode NONE: no trace header, nothing arrives in Langfuse
#   prod     ingestion mode USAGE: trace header, generations arrive with usage and no content
set -euo pipefail
exec python3 -I "$(dirname "${BASH_SOURCE[0]}")/verify.py"
