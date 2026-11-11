"""D3 measurement: how many calls does one gateway request cost Langfuse Web, with the
resolve cache on and off (decision D23). Not part of a benchmark run; run it by hand:

    python3 gateways/langfuse/d3.py [--requests 200] [--threads 4]

It brings the Langfuse stack up (prod profile, ingestion mode USAGE) with the gateway's Web
URL pointed at a counting proxy (d3_proxy.py, container gwbench-d3-proxy), sends the requests
through the gateway, and prints per request: HTTP calls to Web by path, and the extra
Postgres transactions and Redis commands over the idle background rate. First with
LANGFUSE_AI_GATEWAY_CACHE_RESOLVE_ENABLED=true, then false. It tears the stack and the proxy
down at the end. Standard library only, on the host.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROXY = "gwbench-d3-proxy"
GATEWAY = "http://127.0.0.1:18081"
KEY = "sk-lf-gwbench-gateway-key"
COMPOSE = ["docker", "compose", "-f", "docker-compose.base.yml", "-f", "gateways/langfuse/compose.yml"]


def run(cmd, env=None, check=True):
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def pg_xacts():
    out = run(["docker", "exec", "gwbench-langfuse-postgres", "psql", "-U", "postgres", "-tAc",
               "select xact_commit + xact_rollback from pg_stat_database where datname = 'postgres'"])
    return int(out)


def redis_commands():
    out = run(["docker", "exec", "gwbench-langfuse-redis", "redis-cli", "info", "stats"])
    for line in out.splitlines():
        if line.startswith("total_commands_processed:"):
            return int(line.split(":")[1])
    raise RuntimeError("no total_commands_processed")


def proxy_counts():
    out = run(["docker", "exec", PROXY, "python", "-c",
               "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8099/__counts').read().decode())"])
    return json.loads(out)


def proxy_reset():
    run(["docker", "exec", PROXY, "python", "-c",
         "import urllib.request;urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8099/__reset',method='POST'))"])


def send(n, threads):
    todo = list(range(n))
    lock = threading.Lock()
    codes = []

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                todo.pop()
            req = urllib.request.Request(
                f"{GATEWAY}/openai/v1/chat/completions",
                data=json.dumps({"model": "gpt-mock", "messages": [{"role": "user", "content": "d3"}]}).encode(),
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            try:
                codes.append(urllib.request.urlopen(req, timeout=30).status)
            except Exception as e:  # noqa: BLE001
                codes.append(str(e))

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return codes


def measure(cache, n, threads, first):
    env = dict(os.environ, LANGFUSE_AI_GATEWAY_CACHE_RESOLVE_ENABLED=cache,
               LANGFUSE_GATEWAY_WEB_URL="http://d3-proxy:8099", LANGFUSE_INGESTION_MODE="USAGE")
    run(["docker", "rm", "-f", PROXY], check=False)
    if first:
        run(["./scripts/stack.sh", "down", "langfuse"], env=env, check=False)
        print(f"[cache={cache}] starting the stack ...", flush=True)
        run(["./scripts/stack.sh", "up", "langfuse", "--profile", "prod"], env=env)
    else:
        print(f"[cache={cache}] recreating Web and the gateway ...", flush=True)
        run(COMPOSE + ["up", "-d", "--wait", "--force-recreate", "--no-deps", "langfuse-web", "langfuse-ai-gateway"], env=env)
    run(["docker", "run", "-d", "--name", PROXY, "--network", "gwbench", "--network-alias", "d3-proxy",
         "-v", f"{REPO}/gateways/langfuse/d3_proxy.py:/proxy.py:ro", "python:3.13.9-alpine3.22", "python", "-u", "/proxy.py"])
    time.sleep(2)
    send(5, 1)  # warm-up: the first request of a key is a cache miss either way
    time.sleep(8)

    # idle background rate of Postgres and Redis (worker queues, cron jobs)
    i0, p0, r0 = time.time(), pg_xacts(), redis_commands()
    time.sleep(10)
    idle_s = time.time() - i0
    idle_pg = (pg_xacts() - p0) / idle_s
    idle_redis = (redis_commands() - r0) / idle_s

    proxy_reset()
    t0, p0, r0 = time.time(), pg_xacts(), redis_commands()
    codes = send(n, threads)
    busy_s = time.time() - t0
    time.sleep(12)  # let the telemetry batch flush (up to 5 s) and the worker settle
    p1, r1, total_s = pg_xacts(), redis_commands(), time.time() - t0
    counts = proxy_counts()
    ok = sum(1 for c in codes if c == 200)
    print(f"\n== resolve cache {cache}: {n} requests ({ok} x 200), {threads} threads, sent in {busy_s:.1f} s")
    print("   HTTP calls to Web (through the counting proxy):")
    for k, v in sorted(counts.items()):
        print(f"     {k:<48} {v:>5}   {v / n:.3f} per request")
    print(f"   Postgres transactions over idle rate: {(p1 - p0) - idle_pg * total_s:.0f} "
          f"({((p1 - p0) - idle_pg * total_s) / n:.2f} per request; idle {idle_pg:.1f}/s)")
    print(f"   Redis commands over idle rate:        {(r1 - r0) - idle_redis * total_s:.0f} "
          f"({((r1 - r0) - idle_redis * total_s) / n:.2f} per request; idle {idle_redis:.1f}/s)")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    try:
        measure("true", a.requests, a.threads, True)
        measure("false", a.requests, a.threads, False)
    finally:
        run(["docker", "rm", "-f", PROXY], check=False)
        run(["./scripts/stack.sh", "down", "langfuse"], check=False)


main()
