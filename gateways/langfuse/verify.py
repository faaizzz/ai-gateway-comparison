"""Checks that the Langfuse stack is the profile it claims to be (contract rule 6).

Run by verify.sh (so by scripts/smoke.sh and every scripts/run.sh) after the smoke
request, from the repo root, with PROFILE in the environment (and optionally
LANGFUSE_INGESTION_MODE, the variable target.env exports). Standard library only, on the
host: it talks to the gateway on 127.0.0.1:18081 and Web on 127.0.0.1:13030 (the two
published ports) and runs `docker exec` into Postgres.

Both profiles
  - every container of the stack is running, the gateway has the 2 CPU / 2 GB limit and the
    pinned image, Web and the gateway both carry the mock host on the allow-list and neither
    sets NEXT_PUBLIC_LANGFUSE_CLOUD_REGION (decision D2)
  - the organisation's gateway config in Postgres has the expected ingestion mode
    (minimal NONE, prod USAGE, S6 variant FULL)
  - a bad key is refused (401) and a good request is answered by the mock through the gateway
    (Langfuse's own response headers, the mock's model name)
Minimal (NONE): no `langfuse-trace-id` header, and a burst of requests leaves no new
  observation in the Langfuse project after the batch window (traces would take at most
  5 s to be sent plus the ingestion delay, so none within 25 s means none are sent)
Prod (USAGE) and FULL: `langfuse-trace-id` header, and the burst shows up as observations in
  the ingestion project (waited for up to 90 s; the lag is printed). USAGE records token usage
  but no prompt or response, FULL records both.
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

PROFILE = os.environ.get("PROFILE", "prod")
MODE = os.environ.get("LANGFUSE_INGESTION_MODE") or {"prod": "USAGE", "full": "FULL"}.get(PROFILE, "NONE")
GATEWAY = f"http://127.0.0.1:{os.environ.get('LANGFUSE_GATEWAY_HOST_PORT', '18081')}"
WEB = f"http://127.0.0.1:{os.environ.get('LANGFUSE_WEB_HOST_PORT', '13030')}"
KEY = "sk-lf-gwbench-gateway-key"  # TARGET_API_KEY in target.env
PK, SK = "pk-lf-gwbench-local", "sk-lf-gwbench-local"  # LANGFUSE_INIT_PROJECT_*
MODEL = "gpt-mock"
BURST = 30
MINIMAL_WAIT_S = 25
PROD_WAIT_S = 90
IMAGE_TAG = "4.78.0"

fails = 0


def ok(msg):
    print(f"  ok    {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}", file=sys.stderr)


def check(desc, cond):
    (ok if cond else bad)(desc)
    return cond


def sh(*args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def inspect(container, fmt):
    code, out, _ = sh("docker", "inspect", "-f", fmt, container)
    return out if code == 0 else None


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        resp = e
    raw = resp.read().decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    return resp.status, {k.lower(): v for k, v in resp.headers.items()}, parsed


def chat(key=KEY):
    return http(
        "POST",
        f"{GATEWAY}/openai/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {"model": MODEL, "messages": [{"role": "user", "content": "verify"}]},
    )


def observations(since, fields="core,basic,io,usage"):
    """All generations started after `since` (ISO string) in the ingestion project."""
    auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()
    out, cursor = [], None
    while True:
        url = (
            f"{WEB}/api/public/v2/observations?limit=1000&type=GENERATION"
            f"&fields={fields}&fromStartTime={since}"
        )
        if cursor:
            url += f"&cursor={cursor}"
        status, _, body = http("GET", url, {"Authorization": f"Basic {auth}"})
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"observations API answered {status}: {str(body)[:200]}")
        out += body.get("data", [])
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor or not body.get("data"):
            return out


print(f"verify langfuse/{PROFILE} (ingestion mode {MODE})")

# ---- both profiles --------------------------------------------------------
for c in [
    "gwbench-langfuse-ai-gateway",
    "gwbench-langfuse-web",
    "gwbench-langfuse-worker",
    "gwbench-langfuse-postgres",
    "gwbench-langfuse-clickhouse",
    "gwbench-langfuse-redis",
    "gwbench-langfuse-minio",
    "gwbench-langfuse-setup",
]:
    check(f"{c} is running", inspect(c, "{{.State.Running}}") == "true")

GW = "gwbench-langfuse-ai-gateway"
check(f"gateway image is langfuse/ai-gateway:{IMAGE_TAG}", inspect(GW, "{{.Config.Image}}") == f"langfuse/ai-gateway:{IMAGE_TAG}")
check("gateway limit is 2 CPU / 2 GB, no swap", inspect(GW, "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}") == "2000000000 2147483648 2147483648")

for c in (GW, "gwbench-langfuse-web"):
    env = (inspect(c, "{{range .Config.Env}}{{println .}}{{end}}") or "").splitlines()
    check(f"{c}: LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST=mock-llm (D2)", "LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST=mock-llm" in env)
    # The Web image bakes in an empty value, which Web treats as unset.
    check(f"{c}: NEXT_PUBLIC_LANGFUSE_CLOUD_REGION is unset or empty", all(e == "NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=" for e in env if e.startswith("NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=")))
web_env = (inspect("gwbench-langfuse-web", "{{range .Config.Env}}{{println .}}{{end}}") or "").splitlines()
cache = [e for e in web_env if e.startswith("LANGFUSE_AI_GATEWAY_CACHE_RESOLVE_")]
print(f"  info  Web resolve cache: {', '.join(cache)}")

code, out, err = sh(
    "docker", "exec", "gwbench-langfuse-postgres", "psql", "-U", "postgres", "-tAc",
    "select ingestion_mode || '|' || coalesce(default_ingestion_project_id, '') from gateway_configs where organization_id = 'gwbench-org'",
)
check(f"gateway config in Postgres: ingestion mode {MODE}, project gwbench-project", out.lower() == f"{MODE}|gwbench-project".lower())
code, out, err = sh(
    "docker", "exec", "gwbench-langfuse-postgres", "psql", "-U", "postgres", "-tAc",
    "select provider || '|' || base_url || '|' || status from gateway_ai_connections where organization_id = 'gwbench-org'",
)
check("one OpenAI connection to the mock (http://mock-llm:9000/v1), enabled", out.lower() == "openai|http://mock-llm:9000/v1|enabled")

status, _, _ = chat("sk-lf-not-a-real-key")
check("a wrong gateway key is refused with 401", status == 401)

before = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
status, headers, body = chat()
check("request through the gateway answered 200", status == 200)
check("answered by the mock (response model 'mock-upstream')", isinstance(body, dict) and body.get("model") == "mock-upstream")
check("Langfuse response headers present (langfuse-request-id, langfuse-provider: openai)", "langfuse-request-id" in headers and headers.get("langfuse-provider") == "openai")

# ---- ingestion mode ---------------------------------------------------------
if MODE == "NONE":
    check("no langfuse-trace-id header in NONE mode", "langfuse-trace-id" not in headers)
else:
    check(f"langfuse-trace-id header in {MODE} mode", "langfuse-trace-id" in headers)

sent = 1
for _ in range(BURST - 1):
    s, _, _ = chat()
    sent += 1 if s == 200 else 0
check(f"{BURST} requests answered 200", sent == BURST)

if MODE == "NONE":
    print(f"  info  waiting {MINIMAL_WAIT_S} s: nothing may reach Langfuse")
    time.sleep(MINIMAL_WAIT_S)
    try:
        seen = observations(before)
        check(f"no observation in the ingestion project after {BURST} requests (NONE sends nothing)", len(seen) == 0)
    except RuntimeError as e:
        bad(str(e))
else:
    t0 = time.time()
    seen = []
    while time.time() - t0 < PROD_WAIT_S:
        try:
            seen = observations(before)
        except RuntimeError as e:
            bad(str(e))
            break
        if len(seen) >= BURST:
            break
        time.sleep(3)
    lag = time.time() - t0
    # The traces are best effort (not durable), so a short count is reported, not hidden.
    check(f"{BURST} generations arrived in the ingestion project (got {len(seen)}, after {lag:.0f} s)", len(seen) >= BURST)
    if seen:
        g = seen[0]
        usage = g.get("usageDetails") or {}
        check("observation carries token usage (input 1, output 40)", usage.get("input") == 1 and usage.get("output") == 40)
        cost = g.get("totalCost")
        # 1 prompt token at 1 USD per million and 40 completion tokens at 2 USD per million (D19)
        check(f"observation is priced at the mock model's price (81 micro-USD, got {cost})", cost is not None and abs(cost - 0.000081) < 1e-9)
        if MODE == "USAGE":
            check("USAGE records no prompt and no response (input and output null)", g.get("input") is None and g.get("output") is None)
        else:
            check("FULL records prompt and response", g.get("input") is not None and g.get("output") is not None)

if fails:
    print(f"verify langfuse/{PROFILE}: {fails} check(s) failed", file=sys.stderr)
    sys.exit(1)
print(f"verify langfuse/{PROFILE}: all checks passed")
