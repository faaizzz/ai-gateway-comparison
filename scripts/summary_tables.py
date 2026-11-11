#!/usr/bin/env python3
"""Per-scenario summary tables for S2 to S5 (story #9): every target side by side with direct.

    scripts/summary_tables.py <results/date> [--out results/date/SUMMARY-S2-S5.md] [--profile minimal]

Reads results/<date>/<target>/<profile>/<scenario>/run-*/ (summary.json, metadata.json,
resources.csv) for the scenarios s2, s3, s4, s5, s5-resp10k and s5-resp100k, and writes one
markdown file plus a JSON file with the same numbers (all steps, saturated ones included).

Rules it applies:
  - k6 distributions are the primary numbers (D18). p50/p95/p99 are medians over the repeats of
    each target, with the min-max of p50 and p99 over the repeats in brackets when there are
    several runs. Percentiles are never subtracted.
  - Open-loop steps (S2, S3, S5) are judged with the sustained rules (D15 + D24, the same
    thresholds as scripts/sustained.py): achieved >= 95 % of the target, errors < 0.1 %, k6 below
    4 cores, p50 <= 2x the baseline p50. The baseline is the direct S1 p50 at the rate (S2, S5) or
    the direct run of the same scenario (S3). A step that fails in any repeat is marked
    saturated; its latency is replaced by "saturated" in the tables (D15) and kept in the JSON.
  - S4 is closed loop (fixed virtual users): no target rate, so only errors and k6 CPU are
    judged; the table is labelled closed-loop.
  - Gateway-reported overhead is its own labelled column (D18), never ranked.
  - If <date>/SUMMARY-S2-S5-notes.md exists (hand-written observations), it is copied in after the
    header; everything else in the output is generated.
  - CPU is the mean over the step (100 = one core); memory is the peak over the step.
Standard library only.
"""

import argparse
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from summarize import cpu_by_step, load, metric  # noqa: E402
from sustained import (  # noqa: E402
    MAX_ERROR,
    MAX_K6_CPU,
    MIN_ACHIEVED,
    P50_FACTOR,
    baseline_for,
    default_baseline_dirs,
    failed_containers,
    load_baseline,
)

TARGETS = ["direct", "litellm", "bifrost", "langfuse"]
GATEWAY_CONTAINER = {
    "litellm": "gwbench-litellm",
    "bifrost": "gwbench-bifrost",
    "langfuse": "gwbench-langfuse-ai-gateway",
}
OVERHEAD_DEF = {
    "litellm": "its own header x-litellm-overhead-duration-ms (time inside the proxy)",
    "bifrost": "k6 duration minus x-bifrost-upstream-latency-ms (includes the network hops)",
    "langfuse": "none (the gateway sends no overhead header)",
}


def read_csv(path):
    import csv

    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def resources(rows, plan, step, target):
    """Per step: gateway container mean CPU and peak memory, whole stack mean CPU and peak memory.

    The stack is every container of the target except the mock, k6 and the sampler. For
    direct the stack is empty (only the mock runs)."""
    lo = plan["t0_ms"] / 1000.0 + step["fromS"]
    hi = plan["t0_ms"] / 1000.0 + step["toS"]
    gw = GATEWAY_CONTAINER.get(target)
    per_c = {}
    for r in rows:
        ts = float(r["ts"])
        if not (lo <= ts < hi):
            continue
        c = r["container"]
        if "k6-run" in c or "sampler" in c or c == "container":
            continue
        per_c.setdefault(c, []).append((ts, float(r["cpu_percent"]), float(r["mem_bytes"])))
    out = {"gw_cpu": None, "gw_mem": None, "stack_cpu": None, "stack_mem": None, "mock_cpu": None}
    if "gwbench-mock-llm" in per_c:
        out["mock_cpu"] = statistics.mean(v[1] for v in per_c["gwbench-mock-llm"])
    stack = {c: v for c, v in per_c.items() if c != "gwbench-mock-llm"}
    if gw and gw in per_c:
        out["gw_cpu"] = statistics.mean(v[1] for v in per_c[gw])
        out["gw_mem"] = max(v[2] for v in per_c[gw])
    if stack:
        out["stack_cpu"] = sum(statistics.mean(x[1] for x in v) for v in stack.values())
        by_ts = {}
        for v in stack.values():
            for i, x in enumerate(v):
                by_ts[i] = by_ts.get(i, 0.0) + x[2]
        out["stack_mem"] = max(by_ts.values())
    return out


def step_values(run_dir, target):
    summary = load(os.path.join(run_dir, "summary.json"))
    meta = load(os.path.join(run_dir, "metadata.json"))
    plan = (meta.get("k6") or {}).get("plan")
    cpu = cpu_by_step(run_dir, plan)
    rows = read_csv(os.path.join(run_dir, "resources.csv"))
    out = []
    for s in plan["steps"]:
        if s["phase"] != "measure":
            continue
        n = s["name"]
        dur = metric(summary, f"http_req_duration{{step:{n}}}")
        reqs = metric(summary, f"http_reqs{{step:{n}}}")
        failed = metric(summary, f"http_req_failed{{step:{n}}}")
        if not dur or not reqs:
            out.append({"name": n, "rate": s.get("rate"), "vus": s.get("vus"), "no_data": True})
            continue
        secs = s["toS"] - s["fromS"]
        v = {
            "name": n,
            "rate": s.get("rate"),
            "vus": s.get("vus"),
            "request_bytes": s.get("request_bytes"),
            "secs": secs,
            "achieved": reqs.get("count", 0) / secs,
            "requests": reqs.get("count", 0),
            "error": failed.get("rate", failed.get("value", 0)) if failed else 0.0,
            "p50": dur["med"],
            "p95": dur["p(95)"],
            "p99": dur["p(99)"],
            "max": dur["max"],
            "k6_cpu": max([x for k, x in cpu.get(n, {}).items() if "k6-run" in k] or [0.0]),
        }
        ov = metric(summary, f"gateway_overhead_ms{{step:{n}}}")
        if ov and ov.get("max", 0) > 0:
            v["overhead_p50"], v["overhead_p99"] = ov["med"], ov["p(99)"]
        v.update(resources(rows, plan, s, target))
        for m in ("ttft", "itl", "max_gap", "total"):
            t = metric(summary, f"stream_{m}_ms{{step:{n}}}")
            if t:
                v[f"{m}_p50"], v[f"{m}_p95"], v[f"{m}_p99"], v[f"{m}_max"] = t["med"], t["p(95)"], t["p(99)"], t["max"]
        for m in ("faithful", "usage", "failed"):
            r = metric(summary, f"stream_{m}{{step:{n}}}")
            if r:
                tot = r.get("passes", 0) + r.get("fails", 0)
                v[f"stream_{m}"] = r.get("passes", 0) / tot if tot else None
        ch = metric(summary, f"stream_chunks{{step:{n}}}")
        if ch:
            v["chunks_min"], v["chunks_max"] = ch["min"], ch["max"]
        out.append(v)
    return out, meta


def collect(date_dir, scenario, profile):
    """{target: {"runs": [step list per run], "meta": [...]}}"""
    res = {}
    for t in TARGETS:
        prof = "none" if t == "direct" else profile
        runs = sorted(glob.glob(os.path.join(date_dir, t, prof, scenario, "run-*")))
        runs = [r for r in runs if os.path.exists(os.path.join(r, "summary.json"))]
        if not runs:
            continue
        entry = {"run_dirs": runs, "runs": [], "meta": []}
        for r in runs:
            steps, meta = step_values(r, t)
            entry["runs"].append(steps)
            entry["meta"].append(meta)
        res[t] = entry
    return res


def judge(step, base_p50, closed=False):
    """Why a step fails the sustained rules, as (kind, value) pairs; empty if it holds."""
    why = []
    if step.get("no_data"):
        return [("nodata", 0)]
    if not closed and step["achieved"] < MIN_ACHIEVED * step["rate"]:
        why.append(("rate", step["achieved"] / step["rate"] * 100))
    if step["error"] >= MAX_ERROR:
        why.append(("errors", step["error"] * 100))
    if step["k6_cpu"] >= MAX_K6_CPU:
        why.append(("k6", step["k6_cpu"]))
    if not closed and base_p50 is not None and step["p50"] > P50_FACTOR * base_p50:
        why.append(("p50", step["p50"]))
    return why


def why_text(whys, base_p50=None):
    """One sentence for all failing runs of a step: the worst value of each kind."""
    worst = {}
    for w in whys:
        for kind, v in w:
            if kind == "rate":
                worst[kind] = min(worst.get(kind, v), v)
            else:
                worst[kind] = max(worst.get(kind, v), v)
    parts = []
    if "rate" in worst:
        parts.append(f"achieved rate down to {worst['rate']:.0f}% of target")
    if "errors" in worst:
        parts.append(f"errors up to {worst['errors']:.2f}%")
    if "p50" in worst:
        lim = f" (limit 2 x {base_p50:.1f} = {2 * base_p50:.0f} ms)" if base_p50 else ""
        parts.append(f"p50 up to {worst['p50']:.0f} ms{lim}")
    if "k6" in worst:
        parts.append(f"k6 at {worst['k6']:.0f}% CPU")
    if "nodata" in worst:
        parts.append("no data")
    return ", ".join(parts)


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def agg_step(entries, key):
    """Median over repeats of one step's numbers; ranges of p50 and p99."""
    a = {"n": len(entries), "name": key}
    for k in ("rate", "vus", "request_bytes"):
        a[k] = entries[0].get(k)
    nums = {k for e in entries for k, v in e.items() if isinstance(v, (int, float)) and k not in ("rate", "vus", "request_bytes", "secs")}
    for k in nums:
        vals = [e[k] for e in entries if e.get(k) is not None]
        a[k] = med(vals)
        if k in ("p50", "p99", "ttft_p50", "ttft_p99", "itl_p99"):
            a[k + "_range"] = (min(vals), max(vals))
    return a


def fmt(v, digits=1, none="-"):
    return none if v is None else f"{v:.{digits}f}"


def cell(a, k, digits=1):
    v = a.get(k)
    if v is None:
        return "-"
    r = a.get(k + "_range")
    if a["n"] > 1 and r:
        return f"{v:.{digits}f} [{r[0]:.{digits}f}-{r[1]:.{digits}f}]"
    return f"{v:.{digits}f}"


def mb(v):
    return "-" if v is None else f"{v / 1048576:.0f}"


def overhead_cell(a):
    if a.get("overhead_p50") is None:
        return "n/a"
    return f"{a['overhead_p50']:.1f} / {a['overhead_p99']:.1f}"


class Report:
    def __init__(self, date_dir, profile):
        self.date_dir = date_dir
        self.profile = profile
        self.lines = []
        self.data = {}
        self.s1_base = load_baseline(default_baseline_dirs())

    def w(self, s=""):
        self.lines.append(s)

    def scenario_data(self, scenario):
        return collect(self.date_dir, scenario, self.profile)

    def judged(self, scenario, data, closed=False):
        """Per target and step name: aggregated numbers plus the verdict over the repeats."""
        out = {}
        direct_by_step = {}
        if "direct" in data:
            names = [s["name"] for s in data["direct"]["runs"][0]]
            for n in names:
                es = [s for run in data["direct"]["runs"] for s in run if s["name"] == n and not s.get("no_data")]
                if es:
                    direct_by_step[n] = med([e["p50"] for e in es])
        for t, entry in data.items():
            out[t] = {"steps": {}, "n_runs": len(entry["runs"]), "failed_containers": []}
            for meta in entry["meta"]:
                out[t]["failed_containers"] += [c["name"] for c in failed_containers(meta)]
            names = [s["name"] for s in entry["runs"][0]]
            for n in names:
                es = [s for run in entry["runs"] for s in run if s["name"] == n]
                good = [e for e in es if not e.get("no_data")]
                if not good:
                    out[t]["steps"][n] = {"name": n, "n": 0, "no_data": True, "saturated": True, "why": "no data"}
                    continue
                a = agg_step(good, n)
                if scenario in ("s3",):
                    base = direct_by_step.get(n)
                elif a.get("rate"):
                    base = baseline_for(self.s1_base, int(a["rate"]))[0]
                else:
                    base = None
                whys = [judge(e, base, closed) for e in good]
                bad = [e for e, w in zip(good, whys) if w]
                ok = [e for e, w in zip(good, whys) if not w]
                a["baseline_p50"] = base
                a["bad_runs"] = len(bad)
                a["saturated"] = len(bad) > 0
                a["mixed"] = 0 < len(bad) < len(good)
                a["why"] = why_text([w for w in whys if w], base)
                if a["mixed"]:
                    # numbers shown in the tables come from the runs that held; all runs stay in the JSON
                    a["all_runs"] = a.copy()
                    a.update(agg_step(ok, n))
                    a["n_shown"] = len(ok)
                out[t]["steps"][n] = a
        return out

    def label(self, a):
        if a.get("no_data"):
            return "no data"
        if not a["saturated"]:
            return "sustained"
        if a.get("mixed"):
            return f"MIXED: saturated in {a['bad_runs']} of {a['bad_runs'] + a['n']} runs (latency from the {a['n']} that held)"
        return "SATURATED"

    def table_open_loop(self, j, step_names, step_title):
        for n in step_names:
            self.w(f"**{step_title(n, j)}**")
            self.w()
            self.w(
                "| target | runs | status | achieved RPS | errors % | p50 ms | p95 ms | p99 ms | gateway CPU % | gateway MB | stack CPU % | stack MB | gateway-reported overhead p50 / p99 ms |"
            )
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for t in TARGETS:
                if t not in j or n not in j[t]["steps"]:
                    continue
                a = j[t]["steps"][n]
                if a.get("no_data"):
                    self.w(f"| {t} | {j[t]['n_runs']} | no data | | | | | | | | | | |")
                    continue
                sat = a["saturated"] and not a.get("mixed")
                lat = lambda k: "sat." if sat else cell(a, k)  # noqa: E731
                self.w(
                    f"| {t} | {a['n']} | {self.label(a)} | {a['achieved']:.0f} | {a['error'] * 100:.2f} | {lat('p50')} | {lat('p95')} | {lat('p99')} | "
                    f"{fmt(a.get('gw_cpu'), 0)} | {mb(a.get('gw_mem'))} | {fmt(a.get('stack_cpu'), 0)} | {mb(a.get('stack_mem'))} | "
                    f"{'sat.' if sat else overhead_cell(a)} |"
                )
            self.w()
            reasons = [f"{t}: {j[t]['steps'][n]['why']}" for t in TARGETS if t in j and n in j[t]["steps"] and j[t]["steps"][n].get("why")]
            if reasons:
                self.w("Not sustained (D15, D24): " + "; ".join(reasons) + ".")
                self.w()

    def highest_sustained(self, j, step_names):
        parts = []
        for t in TARGETS:
            if t not in j or t == "direct":
                continue
            hi = None
            for n in step_names:
                a = j[t]["steps"].get(n)
                if a and not a.get("no_data") and not a["saturated"]:
                    hi = a
                else:
                    break
            parts.append(f"{t}: " + (f"{hi['name']} ({hi['rate']} RPS)" if hi else "none"))
        return "; ".join(parts)

    def ov_def(self):
        self.w("Gateway-reported overhead (secondary, D18, never ranked): " + "; ".join(f"{t}: {d}" for t, d in OVERHEAD_DEF.items()) + ".")
        self.w()

    def containers_note(self, j):
        bad = {t: sorted(set(v["failed_containers"])) for t, v in j.items() if v["failed_containers"]}
        if bad:
            self.w(f"WARNING: containers died or were OOM-killed: {bad}")
            self.w()

    # ----- scenarios -----

    def s2(self):
        data = self.scenario_data("s2")
        if not data:
            return
        j = self.judged("s2", data)
        self.data["s2"] = j
        names = [s["name"] for s in data[next(iter(data))]["runs"][0]]
        self.w("## S2 - equal-rate steps (open loop, non-streaming)")
        self.w()
        runs = ", ".join(f"{t} {v['n_runs']}" for t, v in j.items())
        self.w(
            f"Rates 100, 250, 500, 1000 RPS for every target, 60 s per step after a 15 s warm-up, mock `MOCK_LATENCY_MS=60`, `{self.profile}` profile. "
            f"Runs per target: {runs}. Latency cells are the median over the repeats with [min-max] of the repeats. "
            "`sat.` means the step is saturated (D15, D24) and its latency is left out; the numbers are in the JSON."
        )
        self.w()
        self.w("Highest sustained ladder step: " + self.highest_sustained(j, names) + ".")
        self.w()
        self.containers_note(j)
        self.table_open_loop(j, names, lambda n, jj: f"S2 at {n[1:]} RPS")
        self.ov_def()

    def s3(self):
        data = self.scenario_data("s3")
        if not data:
            return
        j = self.judged("s3", data)
        self.data["s3"] = j
        names = [s["name"] for s in data[next(iter(data))]["runs"][0]]
        self.w("## S3 - streaming (open loop, SSE through xk6-sse)")
        self.w()
        self.w(
            "Mock streaming defaults: TTFT 20 ms, ITL 5 ms, 40 content chunks (plus a role chunk, a finish chunk, a usage chunk and `[DONE]`). "
            "TTFT = time from sending the request to the first non-empty content delta. Inter-chunk gap = time between two consecutive non-empty content deltas. "
            "Fidelity = share of streams with exactly 40 content chunks, text equal to 40 x `token `, a `finish_reason: stop` and `[DONE]`. "
            f"`{self.profile}` profile. Highest sustained step: " + self.highest_sustained(j, names) + "."
        )
        self.w()
        self.containers_note(j)
        for n in names:
            self.w(f"**S3 at {n[1:]} RPS**")
            self.w()
            self.w(
                "| target | runs | status | achieved RPS | failed % | TTFT p50 | TTFT p95 | TTFT p99 | gap p50 | gap p99 | gap max | stream total p50 | chunks | fidelity % | usage chunk % | gateway CPU % | gateway MB | stack CPU % | stack MB |"
            )
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for t in TARGETS:
                if t not in j or n not in j[t]["steps"]:
                    continue
                a = j[t]["steps"][n]
                if a.get("no_data"):
                    self.w(f"| {t} | {j[t]['n_runs']} | no data |" + " |" * 16)
                    continue
                sat = a["saturated"] and not a.get("mixed")
                lat = lambda k: "sat." if sat else cell(a, k)  # noqa: E731
                chunks = "-" if a.get("chunks_min") is None else (f"{a['chunks_min']:.0f}" if a["chunks_min"] == a["chunks_max"] else f"{a['chunks_min']:.0f}-{a['chunks_max']:.0f}")
                self.w(
                    f"| {t} | {a['n']} | {self.label(a)} | {a['achieved']:.0f} | {(a.get('stream_failed') or 0) * 100:.2f} | {lat('ttft_p50')} | {lat('ttft_p95')} | {lat('ttft_p99')} | "
                    f"{lat('itl_p50')} | {lat('itl_p99')} | {lat('itl_max')} | {lat('total_p50')} | {chunks} | {fmt((a.get('stream_faithful') or 0) * 100, 1) if a.get('stream_faithful') is not None else '-'} | "
                    f"{fmt((a.get('stream_usage') or 0) * 100, 1) if a.get('stream_usage') is not None else '-'} | {fmt(a.get('gw_cpu'), 0)} | {mb(a.get('gw_mem'))} | {fmt(a.get('stack_cpu'), 0)} | {mb(a.get('stack_mem'))} |"
                )
            self.w()
            reasons = [f"{t}: {j[t]['steps'][n]['why']}" for t in TARGETS if t in j and n in j[t]["steps"] and j[t]["steps"][n].get("why")]
            if reasons:
                self.w("Not sustained (D15, D24): " + "; ".join(reasons) + ".")
                self.w()
        self.w("All times in ms. Gateway-reported overhead is not recorded for streams.")
        self.w()

    def s4(self):
        data = self.scenario_data("s4")
        if not data:
            return
        j = self.judged("s4", data, closed=True)
        for t in j.values():
            for a in t["steps"].values():
                a["closed"] = True
        self.data["s4"] = j
        names = [s["name"] for s in data[next(iter(data))]["runs"][0]]
        self.w("## S4 - concurrency sweep (CLOSED LOOP, fixed virtual users)")
        self.w()
        self.w(
            "Closed loop on purpose: each virtual user sends the next request when the previous answer arrives, so the offered rate depends on the latency "
            "and a slower gateway is offered fewer requests. It is not a headline number (contract rule 8) and is not compared by sustained rate. "
            "60 s per level after a 10 s warm-up, non-streaming, mock `MOCK_LATENCY_MS=60`. A level where k6 uses 4 or more cores (or any request fails) is flagged: "
            "the load generator or the target limits it, and the latency is still shown because it is the thing S4 measures. "
            f"`{self.profile}` profile."
        )
        self.w()
        self.containers_note(j)
        for n in names:
            vus = n[1:]
            self.w(f"**S4 at {vus} virtual user{'s' if vus != '1' else ''} (closed loop)**")
            self.w()
            self.w("| target | runs | flags | achieved RPS | errors % | p50 ms | p95 ms | p99 ms | max ms | k6 CPU % | gateway CPU % | gateway MB | stack CPU % | stack MB |")
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for t in TARGETS:
                if t not in j or n not in j[t]["steps"]:
                    continue
                a = j[t]["steps"][n]
                if a.get("no_data"):
                    self.w(f"| {t} | {j[t]['n_runs']} | no data |" + " |" * 11)
                    continue
                flags = a["why"] or "-"
                self.w(
                    f"| {t} | {a['n']} | {flags} | {a['achieved']:.0f} | {a['error'] * 100:.2f} | {cell(a, 'p50')} | {cell(a, 'p95')} | {cell(a, 'p99')} | {fmt(a.get('max'), 0)} | "
                    f"{fmt(a.get('k6_cpu'), 0)} | {fmt(a.get('gw_cpu'), 0)} | {mb(a.get('gw_mem'))} | {fmt(a.get('stack_cpu'), 0)} | {mb(a.get('stack_mem'))} |"
                )
            self.w()

    def s5(self):
        data = self.scenario_data("s5")
        if not data:
            return
        j = self.judged("s5", data)
        self.data["s5"] = j
        names = [s["name"] for s in data[next(iter(data))]["runs"][0]]
        self.w("## S5 - payload sweep (open loop, request size)")
        self.w()
        self.w(
            "Request bodies of about 1 KB, 10 KB and 100 KB (the user message is padded with fixed text) at one fixed rate, non-streaming, mock `MOCK_LATENCY_MS=60`, "
            f"the mock's default 40-token answer, `{self.profile}` profile. Highest sustained size: " + "; ".join(
                f"{t}: " + (", ".join(n for n in names if not j[t]["steps"][n].get("saturated")) or "none") for t in TARGETS if t in j and t != "direct"
            ) + "."
        )
        self.w()
        self.containers_note(j)
        kb = {"b1k": "1 KB", "b10k": "10 KB", "b100k": "100 KB"}
        self.table_open_loop(j, names, lambda n, jj: f"S5 request body {kb.get(n, n)}")
        self.ov_def()

    def s5_resp(self):
        have = []
        for sc, label in (("s5-resp10k", "about 10 KB"), ("s5-resp100k", "about 100 KB")):
            data = self.scenario_data(sc)
            if data:
                have.append((sc, label, data))
        if not have:
            return
        self.w("## S5 - response size (open loop)")
        self.w()
        self.w("Small request, the mock answers with a larger fixed response (`MOCK_OUTPUT_TOKENS` words of `token `). One run per target and size.")
        self.w()
        for sc, label, data in have:
            j = self.judged(sc, data)
            self.data[sc] = j
            names = [s["name"] for s in data[next(iter(data))]["runs"][0]]
            self.table_open_loop(j, names, lambda n, jj, label=label: f"Response {label} at {n[1:]} RPS")
        self.ov_def()

    def header(self):
        self.w("# Results summary: S2 to S5 (story #9)")
        self.w()
        meta = None
        for t in TARGETS:
            for sc in ("s2", "s3"):
                runs = sorted(glob.glob(os.path.join(self.date_dir, t, "none" if t == "direct" else self.profile, sc, "run-1", "metadata.json")))
                if runs:
                    meta = load(runs[0])
                    break
            if meta:
                break
        self.w(f"Generated by `scripts/summary_tables.py` from `{self.date_dir}`. Primary numbers are k6 client-side distributions side by side with `direct` (D18); percentiles are never subtracted.")
        self.w()
        if meta:
            host = meta.get("host", {})
            dk = meta.get("docker", {})
            self.w(
                f"Host: {host.get('cpu', '?')}, Docker Desktop {dk.get('ncpu')} CPUs / {(dk.get('mem_total_bytes') or 0) / 1e9:.1f} GB. "
                f"Mock knobs: {', '.join(f'{k}={v}' for k, v in sorted((meta.get('mock_knobs') or {}).items()) if v and k in ('MOCK_LATENCY_MS', 'MOCK_TTFT_MS', 'MOCK_ITL_MS', 'MOCK_OUTPUT_TOKENS'))}. "
                "Gateway container limit 2 CPU / 2 GB; tuned values per D25 (LiteLLM Granian with 2 workers, Bifrost and Langfuse defaults)."
            )
            self.w()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date_dir")
    ap.add_argument("--out")
    ap.add_argument("--profile", default="minimal")
    a = ap.parse_args()
    rep = Report(a.date_dir.rstrip("/"), a.profile)
    rep.header()
    notes = os.path.join(rep.date_dir, "SUMMARY-S2-S5-notes.md")
    if os.path.exists(notes):
        with open(notes) as f:
            rep.w(f.read().rstrip())
            rep.w()
    rep.s2()
    rep.s3()
    rep.s4()
    rep.s5()
    rep.s5_resp()
    out = a.out or os.path.join(a.date_dir, "SUMMARY-S2-S5.md")
    with open(out, "w") as f:
        f.write("\n".join(rep.lines) + "\n")
    jpath = os.path.splitext(out)[0] + ".json"
    with open(jpath, "w") as f:
        json.dump(rep.data, f, indent=1, default=str)
        f.write("\n")
    print(f"wrote {out} and {jpath}")


if __name__ == "__main__":
    main()
