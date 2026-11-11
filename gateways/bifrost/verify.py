#!/usr/bin/env python3
"""Checks that the Bifrost stack is the profile it claims to be (contract rule 6).

Run through gateways/bifrost/verify.sh (the runner and scripts/smoke.sh call that) from
the repo root, with PROFILE in the environment. Exit code 1 if any check fails.
Standard library only; talks to Bifrost on 127.0.0.1:$BIFROST_HOST_PORT (default 18080,
published on loopback by the compose overlays) and to Docker through the CLI.

  both profiles  the request goes through Bifrost (its x-bifrost-* headers) to the mock
                 (the mock's text and model name), version and image match, the gateway
                 limit is 2 CPU / 2 GB, the tuning knobs are the recorded defaults
  minimal        config store OFF and log store OFF, shown four ways: the mounted config
                 says so, the data directory holds no database file before or after a
                 burst of requests (nothing is written anywhere), the logs API does not
                 exist (HTTP 404, not an empty list), and the logging and telemetry
                 plugins are not loaded (no /metrics either)
  prod           config store and log store ON: both SQLite files exist, the log store
                 file grows during a burst and the logs API lists the burst with the
                 virtual key on every row, governance refuses a missing or wrong key and
                 charges the budget, telemetry answers on /metrics
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROFILE = os.environ.get("PROFILE", "minimal")
GW = "gwbench-bifrost"
IMAGE = "maximhq/bifrost:v2.5.1"
VERSION = "v2.5.1"
BASE = "http://127.0.0.1:" + os.environ.get("BIFROST_HOST_PORT", "18080")
# Same local dummy values as the compose overlays and target.env.
VIRTUAL_KEY = "sk-bf-gwbench-virtual"
OPEN_KEY = "sk-gwbench-open"
SETUP_TOKEN = "gwbench-setup-token"
MODEL = "openai/mock-gpt"
BURST = 30

fails = 0
sys.stdout.reconfigure(line_buffering=True)  # keep ok/FAIL lines in order when stderr is merged


def ok(msg):
    print(f"  ok    {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}", file=sys.stderr)


def check(desc, cond, detail=""):
    if cond:
        ok(desc)
    else:
        bad(f"{desc}{' (' + str(detail) + ')' if detail != '' else ''}")


def req(method, path, key=None, body=None, headers=None, timeout=20):
    """One HTTP call. Returns (status, lower-cased headers, parsed JSON or text)."""
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    raw = resp.read().decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    return resp.status, {k.lower(): v for k, v in resp.headers.items()}, parsed


def chat(key):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "verify"}]}
    return req("POST", "/v1/chat/completions", key, body)


def admin(path):
    """Management API. Minimal: open (no config store, so no auth middleware). Prod: the
    OSS admin API is locked until dashboard auth is on, unless the setup token is sent."""
    return req("GET", path, headers={"x-bifrost-setup-token": SETUP_TOKEN})


def sh(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def in_gw(script):
    return sh("docker", "exec", GW, "sh", "-c", script)[1]


def data_files():
    """Every file under /app/data with its size, as {path: bytes}."""
    out = in_gw("find /app/data -type f -exec wc -c {} +")
    files = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] != "total":
            files[parts[1]] = int(parts[0])
    return files


def db_files(files):
    return {p: n for p, n in files.items() if ".db" in os.path.basename(p)}


print(f"verify bifrost/{PROFILE}")
key = VIRTUAL_KEY if PROFILE == "prod" else OPEN_KEY

# ---- both profiles ---------------------------------------------------------
running = sh("docker", "inspect", "-f", "{{.State.Running}}", GW)[1]
check(f"gateway container {GW} is running", running == "true")
image = sh("docker", "inspect", "-f", "{{.Config.Image}}", GW)[1]
check(f"gateway image is {IMAGE}", image == IMAGE, image)
limits = sh("docker", "inspect", "-f", "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}", GW)[1]
check("gateway limit is 2 CPU / 2 GB", limits == "2000000000 2147483648", limits)

st, _, body = req("GET", "/api/version", headers={"x-bifrost-setup-token": SETUP_TOKEN})
check(f"/api/version is {VERSION}", st == 200 and body == VERSION, f"{st} {body}")

st, h, body = chat(key)
text = ""
if st == 200 and isinstance(body, dict):
    text = body["choices"][0]["message"]["content"][:5]
print(f"  request through the gateway: HTTP {st}, text '{text}', model {body.get('model') if isinstance(body, dict) else '?'}")
check("request answered 200 by the mock via the gateway (mock text)", st == 200 and text == "token")
check(
    "response model is the mock's own name (mock-upstream), not the requested one",
    isinstance(body, dict) and body.get("model") == "mock-upstream",
)
check("response carries x-bifrost-request-id (it went through Bifrost)", bool(h.get("x-bifrost-request-id")))
check(
    "x-bifrost-routing-info-provider is openai (the provider with the mock base URL)",
    h.get("x-bifrost-routing-info-provider") == "openai",
    h.get("x-bifrost-routing-info-provider"),
)
up = h.get("x-bifrost-upstream-latency-ms")
try:
    up_ms = float(up)
except (TypeError, ValueError):
    up_ms = -1.0
check(
    f"x-bifrost-upstream-latency-ms present and plausible for a 60 ms mock (the header k6 derives overhead from): {up}",
    55 <= up_ms <= 200,
)

# Tuning knobs (story #8 changes them): recorded defaults.
st, _, prov = admin("/api/providers")
conc = {}
if st == 200 and isinstance(prov, dict):
    for p in prov.get("providers", []):
        conc[p["name"]] = (p.get("concurrency_and_buffer_size") or {})
oa = conc.get("openai", {})
check(
    "openai provider concurrency 1000 and buffer size 5000 (Bifrost defaults, recorded)",
    oa.get("concurrency") == 1000 and oa.get("buffer_size") == 5000,
    oa,
)
st, _, cfg = admin("/api/config")
client = cfg.get("client_config", {}) if st == 200 and isinstance(cfg, dict) else {}
check("initial_pool_size 5000, drop_excess_requests false (defaults, recorded)", client.get("initial_pool_size") == 5000 and client.get("drop_excess_requests") is False, client)

mounted = in_gw("cat /app/data/config.json")
try:
    conf = json.loads(mounted)
except ValueError:
    conf = {}
check(f"mounted config is config.{PROFILE}.json (file parsed, has providers.openai)", "openai" in conf.get("providers", {}))
check(
    "provider base_url is http://mock-llm:9000 (no /v1: Bifrost appends /v1/chat/completions itself)",
    conf.get("providers", {}).get("openai", {}).get("network_config", {}).get("base_url") == "http://mock-llm:9000",
)

# ---- minimal ---------------------------------------------------------------
if PROFILE == "minimal":
    check("config.json: config_store.enabled is false", conf.get("config_store", {}).get("enabled") is False)
    check("config.json: logs_store.enabled is false", conf.get("logs_store", {}).get("enabled") is False)
    check("config.json: client.enable_logging is false", conf.get("client", {}).get("enable_logging") is False)
    check("/api/config reports enable_logging false (the running process, not just the file)", client.get("enable_logging") is False)

    before = data_files()
    print(f"  data dir before the burst: {sorted(before)}")
    check("no database file (*.db, *.db-wal, *.db-shm) in /app/data before the burst", not db_files(before), db_files(before))
    statuses = [chat(key)[0] for _ in range(BURST)]
    check(f"{BURST} requests through the gateway all answered 200", statuses == [200] * BURST, sorted(set(statuses)))
    time.sleep(3)  # a log writer would have flushed by now (prod shows the entry within 1 s)
    after = data_files()
    check("no database file in /app/data after the burst: nothing was persisted", not db_files(after), db_files(after))
    check("data dir is byte for byte the same after the burst (only config.json)", after == before and list(after) == ["/app/data/config.json"], after)
    check("/app/data/logs holds no files", in_gw("find /app/data/logs -type f | wc -l") == "0")
    for path in ("/api/logs", "/api/logs/stats", "/api/governance/virtual-keys"):
        st, _, b = admin(path)
        check(f"{path} does not exist (HTTP 404: no log store or config store behind it), got {st}", st == 404)
    st, _, _ = admin("/metrics")
    check(f"/metrics does not exist (telemetry plugin off), got HTTP {st}", st == 404)
    st, _, pl = admin("/api/plugins")
    active = sorted(p["name"] for p in pl.get("plugins", []) if p.get("status", {}).get("status") == "active") if st == 200 and isinstance(pl, dict) else None
    print(f"  active plugins: {active}")
    check(
        "no logging, telemetry, otel, semantic-cache or maxim plugin is active",
        active is not None and not any(n in ("logging", "telemetry", "otel", "semantic_cache", "semanticcache", "maxim") for n in active),
        active,
    )
    others = sh("docker", "ps", "--format", "{{.Names}}", "--filter", "name=gwbench-")[1].split()
    extra = [n for n in others if n not in (GW, "gwbench-mock-llm") and not n.startswith(("gwbench-k6", "gwbench-sampler"))]
    check("no other gateway-stack container (no database or setup container)", not extra, extra)

# ---- prod ------------------------------------------------------------------
if PROFILE == "prod":
    check("config.json: config_store is enabled (sqlite)", conf.get("config_store", {}).get("enabled") is True)
    check("config.json: logs_store is enabled (sqlite)", conf.get("logs_store", {}).get("enabled") is True)
    check("/api/config reports enable_logging true and enforce_auth_on_inference true", client.get("enable_logging") is True and client.get("enforce_auth_on_inference") is True)

    before = data_files()
    config_db = before.get("/app/data/config.db", 0)
    logs_db = before.get("/app/data/logs.db", 0)
    check(f"config.db exists ({config_db} bytes)", config_db > 0)
    check(f"logs.db exists ({logs_db} bytes)", "/app/data/logs.db" in before)
    logs_size_before = sum(n for p, n in before.items() if os.path.basename(p).startswith("logs.db"))

    # Governance: a key is required, the right one is accepted and charged.
    st, _, b = chat(None)
    check(f"no key is refused (HTTP 401), got {st}", st == 401, b.get("type") if isinstance(b, dict) else b)
    st, _, b = chat("sk-bf-wrong-key")
    check(f"a wrong virtual key is refused (HTTP 401), got {st}", st == 401)

    st, _, b = admin("/api/logs?limit=1")
    base_total = b.get("pagination", {}).get("total_count", 0) if st == 200 and isinstance(b, dict) else -1
    check(f"logs API answers 200 (the log store is on), got {st}", st == 200)
    statuses = [chat(key)[0] for _ in range(BURST)]
    check(f"{BURST} requests with the virtual key all answered 200", statuses == [200] * BURST, sorted(set(statuses)))

    total = base_total
    deadline = time.time() + 30
    rows = []
    while time.time() < deadline:
        st, _, b = admin("/api/logs?limit=5")
        if st == 200 and isinstance(b, dict):
            total = b.get("pagination", {}).get("total_count", 0)
            rows = b.get("logs", [])
            if total >= base_total + BURST:
                break
        time.sleep(1)
    check(f"logs API lists the burst: total_count {base_total} -> {total} (at least +{BURST})", total >= base_total + BURST)
    row = rows[0] if rows else {}
    check(
        "a log row carries the virtual key, the provider and Bifrost's own overhead",
        row.get("virtual_key_id") == "vk-gwbench" and row.get("provider") == "openai" and row.get("overhead_latency") is not None,
        {k: row.get(k) for k in ("virtual_key_id", "provider", "overhead_latency")},
    )
    after = data_files()
    logs_size_after = sum(n for p, n in after.items() if os.path.basename(p).startswith("logs.db"))
    check(f"the log store file grew ({logs_size_before} -> {logs_size_after} bytes, logs.db + wal)", logs_size_after > logs_size_before)

    # Budget usage is written back asynchronously, a second or two after the requests.
    usage = None
    deadline = time.time() + 30
    while time.time() < deadline:
        st, _, b = admin("/api/governance/budgets")
        if st == 200 and isinstance(b, dict):
            for budget in b.get("budgets", []):
                if budget.get("id") == "budget-gwbench":
                    usage = budget.get("current_usage")
        if isinstance(usage, (int, float)) and usage > 0:
            break
        time.sleep(1)
    check(f"the virtual key's budget is charged (current_usage {usage} USD > 0)", isinstance(usage, (int, float)) and usage > 0)
    st, _, b = admin("/api/governance/virtual-keys")
    names = [v.get("name") for v in b.get("virtual_keys", [])] if st == 200 and isinstance(b, dict) else []
    check(f"virtual key 'gwbench' is stored in the config store (keys: {names})", "gwbench" in names)
    st, _, b = admin("/metrics")
    check(f"/metrics answers with bifrost_ series (telemetry plugin on), got HTTP {st}", st == 200 and isinstance(b, str) and "bifrost_" in b)

if fails:
    print(f"verify bifrost/{PROFILE}: {fails} check(s) FAILED", file=sys.stderr)
    sys.exit(1)
print(f"verify bifrost/{PROFILE}: all checks passed")
