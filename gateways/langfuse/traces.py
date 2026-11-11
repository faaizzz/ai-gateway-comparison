#!/usr/bin/env python3
"""S6 trace delivery: how many of the requests of a run left a trace in the Langfuse backend, and
how long the traces took. Works for both tracing paths of S6: the Langfuse AI gateway (all
ingestion modes) and the LiteLLM Langfuse callback (the same Langfuse stack, project
`gwbench-project`).

    python3 gateways/langfuse/traces.py <run-dir>

Run by scripts/run.sh after k6 (target.env: TARGET_POST_RUN, TARGET_POST_RUN_FOR=s6), while the
stack is still up. Writes <run-dir>/traces.json. Standard library only; talks to ClickHouse with
`docker exec` and to the Langfuse public API on 127.0.0.1:13030.

Method (decision D28). The requests carry no tags, so traces are counted in the time window of the
run instead of by id:

  window   from the start of the k6 test (setup(), line GWBENCH_PLAN in k6.log) minus 2 s to its
           planned end plus 20 s, on the observation's start time. The smoke and verify requests
           run before the window, the sampler and teardown after it.
  sent     every request k6 sent in the run (http_reqs, whole run, warm-up and ramp included).
  found    distinct generation observations (table events_core, Langfuse v4) of the project in the window, counted
           after the ingestion queue drained: the count is polled until it has not grown for
           QUIET_S seconds (at most MAX_WAIT_S). loss = sent - found.
  lag      per trace: the time the worker wrote the row (created_at, set when the worker merges the
           event) minus the end time of the request. It is a lower bound of what an API reader
           sees: ClickHouse is flushed in batches (default every 1 s), after which the row is
           readable. Computed over all traces, so no sample and no load on the API while measuring.
  drain    time from the last request's end to the last trace's write.
  api_sample  a bounded sample (SAMPLE traces spread over the window) is fetched by id through the
           public API (GET /api/public/v2/observations?traceId=<id>) to confirm that what ClickHouse holds is
           served: `checked` and `found`.

A mode without tracing (ingestion NONE, or no callback) reports found 0 and loss 100 %: that is the
expected result there, not a loss.
"""

import base64
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

CH = ["docker", "exec", "gwbench-langfuse-clickhouse", "clickhouse-client", "--user", "clickhouse", "--password", "gwbench-local"]
WEB = f"http://127.0.0.1:{os.environ.get('LANGFUSE_WEB_HOST_PORT', '13030')}"
AUTH = "Basic " + base64.b64encode(b"pk-lf-gwbench-local:sk-lf-gwbench-local").decode()
PROJECT = "gwbench-project"
QUIET_S = 20
MAX_WAIT_S = 240
SAMPLE = 40
BEFORE_S = 2
AFTER_S = 20


def ch(query):
    r = subprocess.run(CH + ["--query", query, "--format", "JSONEachRow"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"clickhouse: {r.stderr.strip()[:300]}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def plan_of(run_dir):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
    from metadata import parse_plan

    plan = parse_plan(os.path.join(run_dir, "k6.log"))
    if not plan:
        raise RuntimeError("no GWBENCH_PLAN line in k6.log")
    return plan


def where(t0, t1):
    return (
        f"project_id = '{PROJECT}' and type = 'GENERATION' and is_deleted = 0 "
        f"and start_time >= toDateTime64({t0:.3f}, 3, 'UTC') and start_time <= toDateTime64({t1:.3f}, 3, 'UTC')"
    )


def count(t0, t1):
    rows = ch(f"select uniqExact(span_id) as n from events_core where {where(t0, t1)}")
    return int(rows[0]["n"])


def pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def api_has_trace(trace_id):
    """True if the v2 observations API serves an observation of the trace (the v4 build has no
    legacy /api/public/traces endpoint)."""
    req = urllib.request.Request(f"{WEB}/api/public/v2/observations?traceId={trace_id}&limit=1&fields=core", headers={"Authorization": AUTH})
    try:
        body = json.load(urllib.request.urlopen(req, timeout=20))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return bool(body.get("data"))


def main():
    run_dir = sys.argv[1].rstrip("/")
    plan = plan_of(run_dir)
    with open(os.path.join(run_dir, "summary.json")) as f:
        summary = json.load(f)
    reqs = summary["metrics"]["http_reqs"]
    sent = int(reqs.get("count", reqs.get("values", {}).get("count", 0)))
    t0 = plan["t0_ms"] / 1000.0 - BEFORE_S
    t1 = plan["t0_ms"] / 1000.0 + plan["total_s"] + AFTER_S

    # wait until the ingestion queue has drained: the count stops growing
    started = time.time()
    last, last_change = -1, time.time()
    while True:
        n = count(t0, t1)
        if n != last:
            last, last_change = n, time.time()
        if time.time() - last_change >= QUIET_S or time.time() - started >= MAX_WAIT_S:
            break
        # nothing arrives in a mode without tracing: do not wait the full quiet time for nothing
        if n == 0 and time.time() - started >= QUIET_S:
            break
        time.sleep(3)
    found = last
    waited = time.time() - started

    rows = ch(
        "select span_id as id, trace_id, toUnixTimestamp64Milli(min(created_at)) / 1000 as written, "
        "toUnixTimestamp64Milli(max(end_time)) / 1000 as ended "
        f"from events_core where {where(t0, t1)} group by span_id, trace_id order by ended"
    )
    lags = [float(r["written"]) - float(r["ended"]) for r in rows if r["ended"] is not None]
    drain = None
    if rows:
        drain = max(float(r["written"]) for r in rows) - max(float(r["ended"]) for r in rows)

    # bounded sample through the public API
    checked, ok = 0, 0
    if rows:
        step = max(1, len(rows) // SAMPLE)
        for r in rows[::step][:SAMPLE]:
            checked += 1
            ok += 1 if api_has_trace(r["trace_id"]) else 0

    out = {
        "method": "see gateways/langfuse/traces.py: traces counted in the run's time window in ClickHouse after the queue drained; lag = row write time minus request end time",
        "window": {"from": t0, "to": t1},
        "sent": sent,
        "found": found,
        "lost": sent - found,
        "loss_pct": (sent - found) / sent * 100 if sent else None,
        "waited_s": round(waited, 1),
        "lag_s": {
            "n": len(lags),
            "p50": pct(lags, 0.5),
            "p95": pct(lags, 0.95),
            "p99": pct(lags, 0.99),
            "max": max(lags) if lags else None,
            "mean": statistics.mean(lags) if lags else None,
        },
        "drain_s": drain,
        "api_sample": {"checked": checked, "found": ok},
    }
    with open(os.path.join(run_dir, "traces.json"), "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    lag = out["lag_s"]
    p50 = f"{lag['p50']:.2f}" if lag["p50"] is not None else "-"
    p99 = f"{lag['p99']:.2f}" if lag["p99"] is not None else "-"
    print(f"traces: sent {sent}, found {found}, lost {sent - found}, lag p50 {p50} s / p99 {p99} s, api sample {ok}/{checked}")


if __name__ == "__main__":
    main()
