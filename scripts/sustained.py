#!/usr/bin/env python3
"""Judge each step of a run against the sustained-step rules (D15 + D24) and print the
highest sustained rate.

    scripts/sustained.py <run-dir> [<run-dir> ...] [--baseline <s1-run-dir> ...] [--json]

A step is sustained when ALL of these hold:
  - achieved request rate >= 95 % of the target rate            (D15)
  - failed requests < 0.1 %                                      (D15)
  - the k6 load generator is not the limit: its mean CPU over the step is under 400 %
    (4 cores, D17)                                               (D15)
  - p50 <= 2 x the direct baseline p50 at the same rate         (D24)
  - no gateway-stack container was OOM-killed or died during the run (a failed run)
The highest sustained rate is the last step before the first step that is not sustained
(the ladder stops at the first failure, as in D17). Steps above it are printed too.

Baselines: p50 of S1 (direct) runs, per rate. By default every
results/*/direct/none/{s1,s1-extended}/run-*/ directory that is not a scratch folder is used (median of
the runs per rate); --baseline adds or replaces (scratch S1 runs at higher rates). A rate
with no baseline uses the baseline of the nearest rate (the direct p50 is flat, 61.3 to
62.4 ms from 100 to 20000 RPS, D8) and the step is marked with '~'.

Reusable for S10 (D17): same rules, same output. Standard library only.
"""

import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from summarize import cpu_by_step, load, metric  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_ACHIEVED = 0.95
MAX_ERROR = 0.001
MAX_K6_CPU = 400.0
P50_FACTOR = 2.0


def step_stats(run_dir):
    """Per measured step: rate, achieved, error rate, p50, p95, p99, per-container CPU."""
    summary = load(os.path.join(run_dir, "summary.json"))
    meta_path = os.path.join(run_dir, "metadata.json")
    meta = load(meta_path) if os.path.exists(meta_path) else {}
    plan = (meta.get("k6") or {}).get("plan")
    if not plan:
        raise ValueError("no step plan in metadata.json")
    cpu = cpu_by_step(run_dir, plan)
    out = []
    for s in plan["steps"]:
        if s["phase"] != "measure":
            continue
        name = s["name"]
        dur = metric(summary, f"http_req_duration{{step:{name}}}")
        reqs = metric(summary, f"http_reqs{{step:{name}}}")
        failed = metric(summary, f"http_req_failed{{step:{name}}}")
        if not dur or not reqs:
            out.append({"rate": s["rate"], "name": name, "no_data": True})
            continue
        c = cpu.get(name, {})
        k6 = max([v for k, v in c.items() if "k6-run" in k] or [0.0])
        non_k6 = {k.replace("gwbench-", ""): v for k, v in c.items() if "k6-run" not in k and "sampler" not in k}
        out.append(
            {
                "rate": s["rate"],
                "name": name,
                "achieved": reqs.get("count", 0) / (s["toS"] - s["fromS"]),
                "error": (failed.get("rate", failed.get("value", 0)) if failed else 0.0),
                "p50": dur["med"],
                "p95": dur["p(95)"],
                "p99": dur["p(99)"],
                "k6_cpu": k6,
                "cpu": non_k6,
            }
        )
    return out, meta


def load_baseline(dirs):
    per_rate = {}
    for d in dirs:
        try:
            steps, _ = step_stats(d)
        except (OSError, ValueError, KeyError):
            continue
        for s in steps:
            if not s.get("no_data"):
                per_rate.setdefault(s["rate"], []).append(s["p50"])
    return {r: statistics.median(v) for r, v in per_rate.items()}


def default_baseline_dirs():
    pats = []
    for scen in ("s1", "s1-extended"):  # s1-extended: S1 direct at 1500 to 6000 RPS, for tuning and S10
        pats += glob.glob(os.path.join(REPO, "results", "*", "direct", "none", scen, "run-*"))
    return [p for p in sorted(pats) if "/results/scratch" not in p and "/results/base-smoke" not in p]


def baseline_for(base, rate):
    if rate in base:
        return base[rate], False
    if not base:
        return None, True
    near = min(base, key=lambda r: abs(r - rate))
    return base[near], True


def failed_containers(meta):
    bad = []
    for c in meta.get("container_states") or []:
        if c.get("oom_killed") or c.get("status") not in ("running", None) or (c.get("restart_count") or 0) > 0:
            bad.append(c)
    return bad


def evaluate(run_dir, base):
    steps, meta = step_stats(run_dir)
    dead = failed_containers(meta)
    rows = []
    highest = None
    stopped = False
    for s in steps:
        row = dict(s)
        if s.get("no_data"):
            row.update(sustained=False, why="no data")
            rows.append(row)
            stopped = True
            continue
        b50, approx = baseline_for(base, s["rate"])
        why = []
        if s["achieved"] < MIN_ACHIEVED * s["rate"]:
            why.append(f"rate {s['achieved'] / s['rate'] * 100:.0f}%")
        if s["error"] >= MAX_ERROR:
            why.append(f"errors {s['error'] * 100:.2f}%")
        if s["k6_cpu"] >= MAX_K6_CPU:
            why.append(f"k6 bound ({s['k6_cpu']:.0f}% CPU)")
        if b50 is not None and s["p50"] > P50_FACTOR * b50:
            why.append(f"p50 {s['p50']:.0f} > 2x{b50:.1f}")
        if dead:
            why.append("container died/OOM: " + ",".join(c["name"] for c in dead))
        row.update(sustained=not why, why="; ".join(why), baseline_p50=b50, baseline_approx=approx)
        rows.append(row)
        if not why and not stopped:
            highest = s["rate"]
        elif why:
            stopped = True
    return rows, highest, meta, dead


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(2)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    runs, bases = [], []
    mode = runs
    for a in args:
        if a == "--baseline":
            mode = bases
        else:
            mode.append(a)
    base = load_baseline(bases or default_baseline_dirs())
    result = []
    for d in runs:
        rows, highest, meta, dead = evaluate(d, base)
        result.append({"run": d, "highest_sustained": highest, "steps": rows, "failed_containers": dead})
        if as_json:
            continue
        print(f"\n{d}")
        print(f"{'step':>7} {'achieved':>9} {'err%':>6} {'p50':>7} {'base':>7} {'p95':>7} {'p99':>7} {'k6cpu':>6}  verdict  (gateway-stack CPU %)")
        for r in rows:
            if r.get("no_data"):
                print(f"{r['name']:>7} no data")
                continue
            b = f"{r['baseline_p50']:.1f}{'~' if r['baseline_approx'] else ''}" if r.get("baseline_p50") else "-"
            cpu = " ".join(f"{k}={v:.0f}" for k, v in sorted(r["cpu"].items()) if k != "mock-llm")
            verdict = "SUSTAINED" if r["sustained"] else "no: " + r["why"]
            print(
                f"{r['name']:>7} {r['achieved']:>9.1f} {r['error'] * 100:>6.2f} {r['p50']:>7.1f} {b:>7} "
                f"{r['p95']:>7.1f} {r['p99']:>7.1f} {r['k6_cpu']:>6.0f}  {verdict}  ({cpu})"
            )
        hi = next((r for r in rows if r.get("sustained") and r["rate"] == highest), None)
        p99 = f", p50 {hi['p50']:.1f} ms, p99 {hi['p99']:.1f} ms" if hi else ""
        print(f"highest sustained rate: {highest if highest is not None else 'none'}{p99}")
        if dead:
            print("FAILED CONTAINERS: " + json.dumps(dead))
    if as_json:
        print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
