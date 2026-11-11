#!/usr/bin/env bash
# Checks that the LiteLLM stack is the profile it claims to be (contract rule 6).
# Run by scripts/run.sh and scripts/smoke.sh after the smoke request, from the
# repo root, with TARGET and PROFILE in the environment. Exit non-zero on any
# mismatch.
#
#   both profiles   the request goes through LiteLLM (it answers with its own
#                   x-litellm-* headers) to the mock (the mock's text), the
#                   worker count and image match, the gateway limit is 2 CPU / 2 GB
#   minimal         no database, Redis or setup containers, no DATABASE_URL, no
#                   callbacks loaded, admin routes that need a database fail
#   prod            Postgres, Redis and the setup container are up, the virtual key has a
#                   budget, and spend for a request shows up on the key and in the
#                   spend logs
set -euo pipefail

PROFILE="${PROFILE:-minimal}"
GW=gwbench-litellm
KEY_MASTER=sk-gwbench-master
KEY_VIRTUAL=sk-gwbench-virtual
fails=0

ok() { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*" >&2; fails=$((fails + 1)); }
check() { # check <description> <command...>
  local d="$1"
  shift
  if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi
}

# gw_py <python code>: runs python inside the gateway container (no port is
# published, and the image has python but no curl). The code gets a helper
# req(method, path, key, body) -> (status, headers, parsed body or text).
gw_py() {
  docker exec -i "$GW" python - <<PYEOF
import json, urllib.request, urllib.error
BASE = "http://127.0.0.1:4000"
def req(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(r, timeout=20)
    except urllib.error.HTTPError as e:
        resp = e
    raw = resp.read().decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    return resp.status, {k.lower(): v for k, v in resp.headers.items()}, parsed
$1
PYEOF
}

echo "verify litellm/$PROFILE"

# ---- both profiles --------------------------------------------------------
check "gateway container $GW is running" test "$(docker inspect -f '{{.State.Running}}' "$GW")" = true
check "gateway image is ghcr.io/berriai/litellm:v1.109.0" \
  test "$(docker inspect -f '{{.Config.Image}}' "$GW")" = ghcr.io/berriai/litellm:v1.109.0
check "gateway limit is 2 CPU / 2 GB" \
  test "$(docker inspect -f '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}' "$GW")" = "2000000000 2147483648"
args="$(docker inspect -f '{{join .Config.Cmd " "}}' "$GW")"
case "$args" in
  *--num_workers=*) ok "worker count: ${args##*--num_workers=}" ;;
  *) bad "no --num_workers in the gateway command: $args" ;;
esac

KEY="$KEY_MASTER"
[[ "$PROFILE" == prod ]] && KEY="$KEY_VIRTUAL"
out="$(gw_py "
st, h, b = req('POST', '/v1/chat/completions', '$KEY',
  {'model': 'mock-gpt', 'messages': [{'role': 'user', 'content': 'verify'}]})
print(json.dumps({'status': st,
  'overhead': h.get('x-litellm-overhead-duration-ms'),
  'call_id': h.get('x-litellm-call-id'),
  'version': h.get('x-litellm-version'),
  'text': b['choices'][0]['message']['content'][:5] if st == 200 else str(b)[:200]}))
" 2>&1 || true)"
echo "  request through the gateway: $out"
j() { printf '%s' "$out" | python3 -c "import json,sys; print(json.loads(sys.stdin.read().splitlines()[-1]).get('$1') or '')" 2>/dev/null || true; }
check "request answered 200 by the mock via the gateway (mock text)" test "$(j status)" = 200 -a "$(j text)" = token
check "response carries x-litellm-call-id (it went through LiteLLM)" test -n "$(j call_id)"
check "response carries x-litellm-overhead-duration-ms (the metric k6 records)" test -n "$(j overhead)"
check "LiteLLM version header is 1.109.0" test "$(j version)" = 1.109.0

# Callbacks the proxy has loaded (/active/callbacks, as class names). LiteLLM always
# has its own built-in hooks (rate limiter, budget limiter, ...) in these lists;
# what matters is whether a logging or spend callback is among them.
cbs="$(gw_py "
d = req('GET', '/active/callbacks', '$KEY_MASTER')[2]
print(' '.join(sorted({str(x).split(' object')[0].split('.')[-1] for k in ('litellm.success_callback', 'litellm._async_success_callback', 'litellm.failure_callback') for x in d.get(k, [])})))
" 2>&1 | tail -n 1 || true)"
echo "  loaded callback classes: $cbs"
check "no Langfuse, OTel, Prometheus or other logging callback is loaded" \
  bash -c "! printf '%s' '$cbs' | grep -Eiq 'langfuse|otel|opentelemetry|prometheus|datadog|s3logger|gcs_bucket|custom_logger|webhook'"

procs="$(docker exec "$GW" sh -c 'ps -eo args' | grep -c 'multiprocessing.spawn' || true)"
want="${args##*--num_workers=}"
check "$want worker process(es) are running (found $procs)" test "$procs" -eq "$want"

# ---- minimal --------------------------------------------------------------
if [[ "$PROFILE" == minimal ]]; then
  for svc in gwbench-litellm-postgres gwbench-litellm-redis gwbench-litellm-setup; do
    check "no $svc container" test -z "$(docker ps -aq --filter "name=^${svc}$")"
  done
  check "no DATABASE_URL in the gateway environment" \
    bash -c "! docker exec $GW sh -c 'test -n \"\${DATABASE_URL:-}\"'"
  check "readiness: db is not connected" \
    bash -c "docker exec $GW python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:4000/health/readiness').read().decode())\" | grep -q 'Not connected'"
  check "no spend-logging callback (DBLogger) is loaded" bash -c "! printf '%s' '$cbs' | grep -Eiq 'dblogger|spendlog'"
  # Needs a database, so must fail without one.
  code="$(gw_py "
st, h, b = req('GET', '/key/list', '$KEY_MASTER')
print(st)
" 2>&1 | tail -n 1 || true)"
  check "/key/list (needs a database) is refused, got HTTP $code" test "$code" -ge 400 -a "$code" -lt 600
  code="$(gw_py "
st, h, b = req('GET', '/spend/logs', '$KEY_MASTER')
print(st)
" 2>&1 | tail -n 1 || true)"
  check "/spend/logs (spend tracking) is refused, got HTTP $code" test "$code" -ge 400 -a "$code" -lt 600
  check "config mounted is config.minimal.yaml (no callbacks or database_url)" \
    bash -c "! docker exec $GW grep -Eq '^[[:space:]]*(database_url|langfuse)' /app/config.yaml"
fi

# ---- prod -----------------------------------------------------------------
if [[ "$PROFILE" == prod ]]; then
  for svc in gwbench-litellm-postgres gwbench-litellm-redis; do
    check "$svc is healthy" test "$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null)" = healthy
  done
  check "setup container is healthy (the virtual key was created)" \
    test "$(docker inspect -f '{{.State.Health.Status}}' gwbench-litellm-setup 2>/dev/null)" = healthy
  check "readiness: db is connected" \
    bash -c "docker exec $GW python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:4000/health/readiness').read().decode())\" | grep -q '\"db\":\"connected\"'"
  check "redis answers PONG" test "$(docker exec gwbench-litellm-redis redis-cli ping)" = PONG
  check "spend-logging callback (_ProxyDBLogger) is loaded" bash -c "printf '%s' '$cbs' | grep -q _ProxyDBLogger"
  # With Redis configured, LiteLLM keeps its spend and rate-limit counters there.
  check "redis holds spend counters (spend:key:*)" \
    test -n "$(docker exec gwbench-litellm-redis redis-cli --scan --pattern 'spend:key:*' | head -n 1)"

  key="$(gw_py "
print(json.dumps(req('GET', '/key/info?key=$KEY_VIRTUAL', '$KEY_MASTER')[2].get('info', {})))
" 2>&1 || true)"
  k() { printf '%s' "$key" | python3 -c "import json,sys; print(json.loads(sys.stdin.read().splitlines()[-1]).get('$1'))" 2>/dev/null || true; }
  check "virtual key has a budget (max_budget=$(k max_budget))" \
    python3 -c "import sys; sys.exit(0 if float('$(k max_budget)') > 0 else 1)"
  spend0="$(k spend)"

  # The request above was charged to the virtual key. Spend is written in
  # batches (proxy_batch_write_at, 10 s), so poll for it.
  deadline=$(($(date +%s) + 45))
  ok_spend=0
  while [[ $(date +%s) -lt $deadline ]]; do
    key="$(gw_py "
print(json.dumps(req('GET', '/key/info?key=$KEY_VIRTUAL', '$KEY_MASTER')[2].get('info', {})))
" 2>&1 || true)"
    if python3 -c "import sys; sys.exit(0 if float('$(k spend)') > 0 else 1)" 2>/dev/null; then ok_spend=1; break; fi
    sleep 3
  done
  if [[ $ok_spend -eq 1 ]]; then ok "spend tracking: key spend is $(k spend) (was $spend0 at the start of the check)"; else bad "spend tracking: key spend still $(k spend) after 45 s"; fi
  logs="$(gw_py "
st, h, b = req('GET', '/spend/logs?api_key=$KEY_VIRTUAL', '$KEY_MASTER')
print(len(b) if isinstance(b, list) else -1)
" 2>&1 | tail -n 1 || true)"
  check "spend logs hold rows for the virtual key ($logs)" test "$logs" -gt 0
fi

if [[ $fails -gt 0 ]]; then
  echo "verify litellm/$PROFILE: $fails check(s) FAILED" >&2
  exit 1
fi
echo "verify litellm/$PROFILE: all checks passed"
