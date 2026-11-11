#!/usr/bin/env python3
"""Print a per-step table for one or more run directories.

    scripts/summarize.py results/<date>/<target>/<profile>/<scenario>/run-1 [...]

If the gateway reports its own overhead (LiteLLM: x-litellm-overhead-duration-ms, the
k6 metric gateway_overhead_ms), a second table shows its p50/p95/p99 per step,
labelled gateway-reported: it is a different quantity from the k6 latency above.
Reads summary.json (the per-step sub-metrics written by k6/lib/arrival.js),
metadata.json (the step plan) and resources.csv. Columns: target rate, achieved
requests per second, p50/p95/p99 of http_req_duration in ms, failed request
share, and CPU of the busiest non-k6 container during the step (100 = one core).
Standard library only.
"""

import csv
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def metric(summary, name):
    """k6 summary export: metrics are flat dicts (older versions nest under 'values')."""
    m = summary.get("metrics", {}).get(name)
    if m is None:
        return None
    return m.get("values", m)


def cpu_by_step(run_dir, plan):
    """Mean and max CPU per container, per measured step, from resources.csv."""
    path = os.path.join(run_dir, "resources.csv")
    if not plan or not os.path.exists(path):
        return {}
    t0 = plan["t0_ms"] / 1000.0
    out = {}
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for step in plan["steps"]:
        if step["phase"] != "measure":
            continue
        lo, hi = t0 + step["fromS"], t0 + step["toS"]
        per = {}
        for r in rows:
            ts = float(r["ts"])
            if lo <= ts < hi:
                per.setdefault(r["container"], []).append(float(r["cpu_percent"]))
        out[step["name"]] = {c: sum(v) / len(v) for c, v in per.items() if v}
    return out


def short(container):
    name = container.replace("gwbench-", "")
    return "k6" if name.startswith("k6-run") else name


def summarize(run_dir):
    summary = load(os.path.join(run_dir, "summary.json"))
    meta = load(os.path.join(run_dir, "metadata.json")) if os.path.exists(os.path.join(run_dir, "metadata.json")) else {}
    plan = (meta.get("k6") or {}).get("plan")
    cpu = cpu_by_step(run_dir, plan)
    print(f"\n{run_dir}")
    print(f"{'step':>8} {'target':>7} {'achieved':>9} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8} {'failed':>7}  cpu% (mean per container)")
    steps = [s for s in (plan or {}).get("steps", []) if s["phase"] == "measure"]
    for s in steps:
        name = s["name"]
        dur = metric(summary, f"http_req_duration{{step:{name}}}")
        reqs = metric(summary, f"http_reqs{{step:{name}}}")
        failed = metric(summary, f"http_req_failed{{step:{name}}}")
        if not dur or not reqs:
            print(f"{name:>8} (no data)")
            continue
        achieved = reqs.get("count", 0) / (s["toS"] - s["fromS"])
        fail = failed.get("rate", failed.get("value", 0)) if failed else 0
        c = " ".join(f"{short(k)}={v:.0f}" for k, v in sorted(cpu.get(name, {}).items()) if "sampler" not in k)
        print(
            f"{name:>8} {s['rate']:>7} {achieved:>9.1f} {dur['med']:>8.2f} {dur['p(95)']:>8.2f} "
            f"{dur['p(99)']:>8.2f} {dur['max']:>8.1f} {fail * 100:>6.2f}%  {c}"
        )
    rows = []
    for s in steps:
        ov = metric(summary, f"gateway_overhead_ms{{step:{s['name']}}}")
        # A Trend that never got a sample reports all zeros (direct, or a gateway without the header).
        if ov and ov.get("max", 0) > 0:
            rows.append((s["name"], ov))
    if rows:
        print("gateway-reported overhead (ms, from the response header; not comparable to the k6 latency):")
        for name, ov in rows:
            print(f"{name:>8} {'':>7} {'':>9} {ov['med']:>8.2f} {ov['p(95)']:>8.2f} {ov['p(99)']:>8.2f} {ov['max']:>8.1f}")
    dropped = metric(summary, "dropped_iterations")
    if dropped:
        print(f"dropped_iterations (whole run, incl. warm-up): {dropped.get('count', 0):.0f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    for d in sys.argv[1:]:
        try:
            summarize(d)
        except (OSError, ValueError, KeyError) as e:
            print(f"summarize: {d}: {e}", file=sys.stderr)
            sys.exit(1)
