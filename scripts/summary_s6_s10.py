#!/usr/bin/env python3
"""Summary tables for S6 to S10 (story #10). Called by scripts/summary_tables.py --set s6-s10;
can also be run on its own:

    scripts/summary_s6_s10.py <results/date> [--out results/date/SUMMARY-S6-S10.md]

Reads results/<date>/<target>/<profile>/<scenario>/run-*/ (summary.json, metadata.json,
resources.csv, and traces.json for the tracing configurations) for the scenarios s6, s7,
s8-fail, s8-429, s9 and s10, and writes one markdown file and a JSON file with the same numbers.
If <date>/SUMMARY-S6-S10-notes.md exists (hand-written observations) it is copied in after the
header; everything else is generated.

Rules (same as summary_tables.py): k6 client-side distributions are the primary numbers,
medians over the repeats with [min-max] when there are several runs; percentiles are never
subtracted from each other. CPU is the mean over the measured window (100 = one core), memory
the peak. "Deltas" are only given for CPU and memory (additive quantities), latency is shown side
by side. S10 uses the sustained rules of scripts/sustained.py (D15 + D24, k6 under 4 cores).
Standard library only.
"""

import argparse
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from summarize import load, metric  # noqa: E402
from summary_tables import (  # noqa: E402
    GATEWAY_CONTAINER,
    cell,
    fmt,
    mb,
    med,
    read_csv,
    step_values,
)
from sustained import default_baseline_dirs, evaluate, load_baseline  # noqa: E402

TARGETS = ["direct", "litellm", "bifrost", "langfuse"]

# Display order and meaning of the profiles per target, per scenario.
S6_CONFIGS = [
    ("litellm", "minimal", "no database, no callbacks"),
    ("litellm", "prod", "Postgres + Redis + virtual key, tracing off"),
    ("litellm", "prod-langfuse", "prod + Langfuse callback (traces to the Langfuse stack)"),
    ("bifrost", "minimal", "log store off"),
    ("bifrost", "logs", "log store on (SQLite), logging plugin on, nothing else"),
    ("bifrost", "prod", "prod: SQLite stores, logging, telemetry, virtual key, budget"),
    ("langfuse", "minimal", "ingestion NONE (no traces)"),
    ("langfuse", "prod", "ingestion USAGE (tokens and cost, no content)"),
    ("langfuse", "full", "ingestion FULL (prompt and response too)"),
]
S7_CONFIGS = [
    ("litellm", "minimal", "master key only"),
    ("litellm", "prod", "virtual key + budget + Postgres/Redis spend tracking"),
    ("litellm", "prod-rl", "prod + rpm/tpm/parallel limits on the key (never hit)"),
    ("bifrost", "minimal", "no auth, no stores"),
    ("bifrost", "prod", "virtual key + budget + SQLite stores + logging + telemetry"),
    ("bifrost", "prod-rl", "prod + request and token rate limit on the key (never hit)"),
    ("langfuse", "minimal", "gateway key resolved by Web on every request (ingestion NONE)"),
    ("langfuse", "prod", "same key check, ingestion USAGE"),
]
S8_CONFIGS = [
    ("direct", "none", "no gateway, no retries"),
    ("litellm", "minimal", "no retries"),
    ("litellm", "retry", "2 retries"),
    ("bifrost", "minimal", "no retries"),
    ("bifrost", "retry", "2 retries"),
    ("langfuse", "minimal", "no retries (the gateway has none)"),
]


def run_dirs(date_dir, target, profile, scenario):
    runs = sorted(glob.glob(os.path.join(date_dir, target, profile, scenario, "run-*")), key=lambda p: int(p.rsplit("-", 1)[1]))
    return [r for r in runs if os.path.exists(os.path.join(r, "summary.json")) and os.path.exists(os.path.join(r, "metadata.json"))]


def agg_runs(date_dir, target, profile, scenario, step=None):
    """Median over the repeats of one step (default: the first measured step)."""
    runs = run_dirs(date_dir, target, profile, scenario)
    if not runs:
        return None
    entries, metas = [], []
    for r in runs:
        steps, meta = step_values(r, target)
        s = [x for x in steps if (step is None or x["name"] == step) and not x.get("no_data")]
        if s:
            entries.append(s[0])
            metas.append(meta)
    if not entries:
        return None
    a = {"n": len(entries), "run_dirs": runs}
    keys = {k for e in entries for k, v in e.items() if isinstance(v, (int, float)) and k not in ("secs",)}
    for k in keys:
        vals = [e[k] for e in entries if e.get(k) is not None]
        a[k] = med(vals)
        if k in ("p50", "p95", "p99"):
            a[k + "_range"] = (min(vals), max(vals))
    a["name"] = entries[0]["name"]
    a["rate"] = entries[0].get("rate")
    a["metas"] = metas
    a["dead"] = sorted({c["name"] for m in metas for c in (m.get("container_states") or []) if c.get("oom_killed") or (c.get("restart_count") or 0) > 0})
    return a


def container_table(run_dir):
    """Mean CPU and peak memory per container of a run over its first measured step."""
    meta = load(os.path.join(run_dir, "metadata.json"))
    plan = (meta.get("k6") or {}).get("plan")
    step = next(s for s in plan["steps"] if s["phase"] == "measure")
    lo = plan["t0_ms"] / 1000.0 + step["fromS"]
    hi = plan["t0_ms"] / 1000.0 + step["toS"]
    per = {}
    for r in read_csv(os.path.join(run_dir, "resources.csv")):
        ts = float(r["ts"])
        c = r["container"]
        if not (lo <= ts < hi) or "k6-run" in c or "sampler" in c or c == "container":
            continue
        per.setdefault(c.replace("gwbench-", ""), []).append((float(r["cpu_percent"]), float(r["mem_bytes"])))
    return {c: (statistics.mean(x[0] for x in v), max(x[1] for x in v)) for c, v in per.items()}


def pct(v, digits=2):
    return "-" if v is None else f"{v * 100:.{digits}f}"


class Report:
    def __init__(self, date_dir):
        self.date_dir = date_dir
        self.lines = []
        self.data = {}
        self.base = load_baseline(default_baseline_dirs())

    def w(self, s=""):
        self.lines.append(s)

    # ---------------------------------------------------------------- S6 / S7 tables
    def config_table(self, key, scenario, configs, with_overhead=True):
        rows = []
        for target, profile, what in configs:
            a = agg_runs(self.date_dir, target, profile, scenario)
            if a:
                rows.append((target, profile, what, a))
        if not rows:
            return {}
        self.w("| target / profile | what | runs | achieved RPS | errors % | p50 ms | p95 ms | p99 ms | gateway CPU % | gateway MB | stack CPU % | stack MB | gateway-reported overhead p50 / p99 ms |")
        self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        out = {}
        for target, profile, what, a in rows:
            ov = "n/a" if a.get("overhead_p50") is None else f"{a['overhead_p50']:.1f} / {a['overhead_p99']:.1f}"
            self.w(
                f"| {target} / {profile} | {what} | {a['n']} | {a['achieved']:.0f} | {pct(a['error'])} | {cell(a, 'p50')} | {cell(a, 'p95')} | {cell(a, 'p99')} | "
                f"{fmt(a.get('gw_cpu'), 0)} | {mb(a.get('gw_mem'))} | {fmt(a.get('stack_cpu'), 0)} | {mb(a.get('stack_mem'))} | {ov} |"
            )
            out[f"{target}/{profile}"] = {k: v for k, v in a.items() if k not in ("metas", "run_dirs")}
            out[f"{target}/{profile}"]["run_dirs"] = a["run_dirs"]
        self.w()
        dead = {f"{t}/{p}": a["dead"] for t, p, _, a in rows if a["dead"]}
        if dead:
            self.w(f"WARNING: containers OOM-killed or restarted: {dead}")
            self.w()
        self.data[key] = out
        return {(t, p): a for t, p, _, a in rows}

    def deltas(self, rows, pairs):
        """CPU and memory differences (additive quantities) between two configurations of one target."""
        lines = []
        for target, off, on in pairs:
            a, b = rows.get((target, off)), rows.get((target, on))
            if not a or not b:
                continue

            def d(k):
                if a.get(k) is None or b.get(k) is None:
                    return "-"
                return f"{b[k] - a[k]:+.0f}"

            def dm(k):
                if a.get(k) is None or b.get(k) is None:
                    return "-"
                return f"{(b[k] - a[k]) / 1048576:+.0f}"

            lines.append(f"| {target}: {off} -> {on} | {d('gw_cpu')} | {dm('gw_mem')} | {d('stack_cpu')} | {dm('stack_mem')} |")
        if lines:
            self.w("Resource deltas (additive, from the medians above):")
            self.w()
            self.w("| change | gateway CPU % | gateway MB | stack CPU % | stack MB |")
            self.w("| --- | --- | --- | --- | --- |")
            for ln in lines:
                self.w(ln)
            self.w()

    def has_runs(self, configs, scenario):
        return any(run_dirs(self.date_dir, t, p, scenario) for t, p, _ in configs)

    def s6(self):
        if not self.has_runs(S6_CONFIGS, "s6"):
            return
        self.w("## S6 - tracing and logging on vs off (open loop, 100 RPS)")
        self.w()
        self.w(
            "One step of 60 s at 100 RPS after a 15 s warm-up, non-streaming, mock `MOCK_LATENCY_MS=60`. Same traffic for every configuration; only the profile differs. "
            "Latency is side by side; the differences of CPU and memory are additive and shown below the table."
        )
        self.w()
        rows = self.config_table("s6", "s6", S6_CONFIGS)
        if not rows:
            return
        self.deltas(
            rows,
            [
                ("litellm", "prod", "prod-langfuse"),
                ("bifrost", "minimal", "logs"),
                ("bifrost", "minimal", "prod"),
                ("langfuse", "minimal", "prod"),
                ("langfuse", "minimal", "full"),
                ("langfuse", "prod", "full"),
            ],
        )
        # trace lag and loss
        tr = []
        for target, profile, what in S6_CONFIGS:
            for r in run_dirs(self.date_dir, target, profile, "s6"):
                p = os.path.join(r, "traces.json")
                if os.path.exists(p):
                    tr.append((target, profile, what, r, load(p)))
        if tr:
            self.w("**Trace delivery (lag and loss).** `sent` is the number of requests k6 sent in the whole run (warm-up included); `found` the number of traces the Langfuse "
                   "backend holds for that window, counted after the ingestion queue drained (method in the notes). Lag is the time from the end of a request to the moment "
                   "its trace row was written to ClickHouse (so it is readable through the API), per trace; drain is the time from the end of the load until the last trace was written.")
            self.w()
            self.w("| target / profile | run | sent | found | lost | loss % | lag p50 s | lag p95 s | lag p99 s | lag max s | drain s | API sample found |")
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for target, profile, what, r, t in tr:
                lag = t.get("lag_s") or {}
                samp = t.get("api_sample") or {}
                self.w(
                    f"| {target} / {profile} | {r.rsplit('-', 1)[1]} | {t.get('sent')} | {t.get('found')} | {t.get('lost')} | {fmt(t.get('loss_pct'), 2)} | "
                    f"{fmt(lag.get('p50'), 2)} | {fmt(lag.get('p95'), 2)} | {fmt(lag.get('p99'), 2)} | {fmt(lag.get('max'), 2)} | {fmt(t.get('drain_s'), 1)} | "
                    f"{samp.get('found', '-')} of {samp.get('checked', '-')} |"
                )
            self.w()
            self.data["s6_traces"] = [{"target": t_, "profile": p_, "run": r_, **tj} for t_, p_, _, r_, tj in tr]
        # backend breakdown for the LiteLLM callback
        rd = run_dirs(self.date_dir, "litellm", "prod-langfuse", "s6")
        if rd:
            ct = container_table(rd[0])
            self.w("**Whole stack of litellm / prod-langfuse (first run)**: mean CPU % (100 = one core) and peak MB per container. The Langfuse containers are the tracing backend.")
            self.w()
            self.w("| container | CPU % | MB |")
            self.w("| --- | --- | --- |")
            for c, (cpu, m) in sorted(ct.items()):
                self.w(f"| {c} | {cpu:.0f} | {m / 1048576:.0f} |")
            self.w()
        rd = run_dirs(self.date_dir, "langfuse", "full", "s6")
        if rd:
            ct = container_table(rd[0])
            self.w("**Whole stack of langfuse / full (first run)**:")
            self.w()
            self.w("| container | CPU % | MB |")
            self.w("| --- | --- | --- |")
            for c, (cpu, m) in sorted(ct.items()):
                self.w(f"| {c} | {cpu:.0f} | {m / 1048576:.0f} |")
            self.w()

    def s7(self):
        if not self.has_runs(S7_CONFIGS, "s7"):
            return
        self.w("## S7 - auth, virtual keys, budget and rate limits (open loop, 100 RPS)")
        self.w()
        self.w(
            "One step of 60 s at 100 RPS after a 15 s warm-up, non-streaming, mock `MOCK_LATENCY_MS=60`. `minimal` has no key checks, `prod` has a virtual key, a budget "
            "and spend tracking, `prod-rl` adds a rate limit that is configured far above the load and never hit. Only one key is used (a many-keys variant was not run)."
        )
        self.w()
        rows = self.config_table("s7", "s7", S7_CONFIGS)
        if not rows:
            return
        self.deltas(
            rows,
            [
                ("litellm", "minimal", "prod"),
                ("litellm", "prod", "prod-rl"),
                ("bifrost", "minimal", "prod"),
                ("bifrost", "prod", "prod-rl"),
                ("langfuse", "minimal", "prod"),
            ],
        )

    # ---------------------------------------------------------------- S8
    def s8(self):
        found = False
        for sc, label in (("s8-fail", "5 % of upstream answers are HTTP 500 (`MOCK_FAIL_RATE=0.05`)"), ("s8-429", "5 % of upstream answers are HTTP 429 with `Retry-After: 1` (`MOCK_429_RATE=0.05`)")):
            rows = []
            for target, profile, what in S8_CONFIGS:
                rd = run_dirs(self.date_dir, target, profile, sc)
                if not rd:
                    continue
                vals = []
                for r in rd:
                    summary = load(os.path.join(r, "summary.json"))
                    meta = load(os.path.join(r, "metadata.json"))
                    plan = (meta.get("k6") or {}).get("plan")
                    step = next(s for s in plan["steps"] if s["phase"] == "measure")
                    n = step["name"]
                    reqs = (metric(summary, f"http_reqs{{step:{n}}}") or {}).get("count", 0)
                    failed = metric(summary, f"http_req_failed{{step:{n}}}") or {}
                    c = {k: (metric(summary, f"resp_{k}{{step:{n}}}") or {}).get("count", 0) for k in ("2xx", "429", "5xx", "other")}
                    ok = metric(summary, f"ok_ms{{step:{n}}}") or {}
                    fl = metric(summary, f"fail_ms{{step:{n}}}") or {}
                    vals.append(
                        {
                            "run": r.rsplit("-", 1)[1],
                            "requests": reqs,
                            "success": 1 - failed.get("rate", failed.get("value", 0)),
                            "c": c,
                            "ok_p50": ok.get("med"),
                            "ok_p95": ok.get("p(95)"),
                            "ok_p99": ok.get("p(99)"),
                            "ok_max": ok.get("max"),
                            "fail_p50": fl.get("med") if c["429"] + c["5xx"] + c["other"] else None,
                            "knobs": {k: v for k, v in (meta.get("mock_knobs") or {}).items() if k in ("MOCK_FAIL_RATE", "MOCK_429_RATE", "MOCK_FAIL_STATUS", "MOCK_RETRY_AFTER_S") and v},
                        }
                    )
                rows.append((target, profile, what, vals))
            if not rows:
                continue
            if not found:
                self.w("## S8 - fault injection (open loop, 100 RPS)")
                self.w()
                self.w(
                    "One step of 60 s at 100 RPS after a 15 s warm-up, non-streaming. The mock fails a share of the upstream requests; the table is what the client (k6) sees. "
                    "`retry` profiles retry twice (the same count everywhere that supports retries; the Langfuse gateway has no retries); `minimal` does not retry. "
                    "Success % is the share of requests that ended in a 2xx for the client. Latency columns are for the successful requests, `failed p50` for the failed ones."
                )
                self.w()
                found = True
            self.w(f"**{label}**")
            self.w()
            self.w("| target / profile | what | run | requests | client success % | 2xx | 429 | 5xx | other | ok p50 ms | ok p95 ms | ok p99 ms | ok max ms | failed p50 ms |")
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            out = []
            for target, profile, what, vals in rows:
                for v in vals:
                    c = v["c"]
                    self.w(
                        f"| {target} / {profile} | {what} | {v['run']} | {v['requests']:.0f} | {v['success'] * 100:.2f} | {c['2xx']:.0f} | {c['429']:.0f} | {c['5xx']:.0f} | {c['other']:.0f} | "
                        f"{fmt(v['ok_p50'])} | {fmt(v['ok_p95'])} | {fmt(v['ok_p99'])} | {fmt(v['ok_max'], 0)} | {fmt(v['fail_p50'])} |"
                    )
                    out.append({"target": target, "profile": profile, **v})
            self.w()
            self.data[sc] = out

    # ---------------------------------------------------------------- S9
    def mem_series(self, run_dir, plan, containers):
        """[(seconds since the measured part started, bytes)] summed over `containers` (per second bucket)."""
        t_lo = plan["t0_ms"] / 1000.0 + plan["warmup_s"]
        t_hi = plan["t0_ms"] / 1000.0 + plan["total_s"]
        buckets = {}
        seen = {}
        for r in read_csv(os.path.join(run_dir, "resources.csv")):
            c = r["container"].replace("gwbench-", "")
            if not containers(c):
                continue
            ts = float(r["ts"])
            if not (t_lo <= ts <= t_hi):
                continue
            sec = int(ts - t_lo)
            buckets.setdefault(sec, {})[c] = float(r["mem_bytes"])
            seen[c] = True
        out = []
        for sec in sorted(buckets):
            if len(buckets[sec]) == len(seen):  # only seconds where every container has a sample
                out.append((sec, sum(buckets[sec].values())))
        return out

    @staticmethod
    def slope_per_10min(series):
        if len(series) < 10:
            return None
        xs = [s for s, _ in series]
        ys = [b / 1048576.0 for _, b in series]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        return None if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 600

    def s9(self):
        entries = []
        for target in TARGETS:
            prof = "none" if target == "direct" else "minimal"
            for r in run_dirs(self.date_dir, target, prof, "s9"):
                entries.append((target, prof, r))
        if not entries:
            return
        self.w("## S9 - soak (open loop, 100 RPS, 30 minutes)")
        self.w()
        self.w(
            "Six windows of 300 s (w1 to w6) at 100 RPS, non-streaming, mock `MOCK_LATENCY_MS=60`, `minimal` profile, after a 15 s warm-up. Latency per window is the k6 distribution "
            "of that window. Memory growth is the least-squares slope of the container's memory (1 s samples) over the 30 minutes, in MiB per 10 minutes; "
            "also given as the first and last minute averages. `direct` runs the mock only (the control: drift there is the load generator or the mock)."
        )
        self.w()
        self.w("| target | window | requests | errors % | p50 ms | p95 ms | p99 ms | max ms | gateway CPU % | stack CPU % | gateway MB (peak) | stack MB (peak) |")
        self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        mem_rows = []
        out = {}
        for target, prof, r in entries:
            steps, meta = step_values(r, target)
            plan = (meta.get("k6") or {}).get("plan")
            for s in steps:
                if s.get("no_data"):
                    self.w(f"| {target} | {s['name']} | no data |" + " |" * 9)
                    continue
                self.w(
                    f"| {target} | {s['name']} | {s['requests']:.0f} | {pct(s['error'])} | {fmt(s['p50'])} | {fmt(s['p95'])} | {fmt(s['p99'])} | {fmt(s['max'], 0)} | "
                    f"{fmt(s.get('gw_cpu'), 0)} | {fmt(s.get('stack_cpu'), 0)} | {mb(s.get('gw_mem'))} | {mb(s.get('stack_mem'))} |"
                )
            gw = GATEWAY_CONTAINER.get(target, "").replace("gwbench-", "")
            gw_series = self.mem_series(r, plan, lambda c, gw=gw: c == gw) if gw else []
            stack_series = self.mem_series(r, plan, lambda c: not (c == "mock-llm" or c.startswith("k6-run") or "sampler" in c or c == "container"))
            mock_series = self.mem_series(r, plan, lambda c: c == "mock-llm")

            def ends(series):
                if len(series) < 120:
                    return None, None
                return statistics.mean(b for _, b in series[:60]) / 1048576, statistics.mean(b for _, b in series[-60:]) / 1048576

            row = {"target": target, "run": r}
            for label, series in (("gateway", gw_series), ("stack", stack_series), ("mock", mock_series)):
                f, last = ends(series)
                row[label] = {"slope_mib_per_10min": self.slope_per_10min(series), "first_min_mib": f, "last_min_mib": last}
            p99s = [s["p99"] for s in steps if not s.get("no_data")]
            p50s = [s["p50"] for s in steps if not s.get("no_data")]
            row["p99_windows"] = p99s
            row["p50_windows"] = p50s
            row["errors_total"] = sum(s["error"] * s["requests"] for s in steps if not s.get("no_data"))
            row["requests_total"] = sum(s["requests"] for s in steps if not s.get("no_data"))
            row["dead"] = sorted(c["name"] for c in (meta.get("container_states") or []) if c.get("oom_killed") or (c.get("restart_count") or 0) > 0)
            mem_rows.append(row)
            out[target] = row
        self.w()
        self.w("**Memory growth and drift over the 30 minutes**")
        self.w()
        self.w(
            "| target | gateway MiB / 10 min | gateway first min -> last min MiB | stack MiB / 10 min | stack first min -> last min MiB | mock MiB / 10 min | "
            "p50 w1 -> w6 ms | p99 w1 -> w6 ms | p99 min-max over windows | errors total |"
        )
        self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in mem_rows:
            def sl(k):
                return fmt(row[k]["slope_mib_per_10min"], 1)

            def fl(k):
                f, last = row[k]["first_min_mib"], row[k]["last_min_mib"]
                return "-" if f is None else f"{f:.0f} -> {last:.0f}"

            p50s, p99s = row["p50_windows"], row["p99_windows"]
            self.w(
                f"| {row['target']} | {sl('gateway')} | {fl('gateway')} | {sl('stack')} | {fl('stack')} | {sl('mock')} | "
                f"{fmt(p50s[0]) if p50s else '-'} -> {fmt(p50s[-1]) if p50s else '-'} | {fmt(p99s[0]) if p99s else '-'} -> {fmt(p99s[-1]) if p99s else '-'} | "
                f"{fmt(min(p99s)) if p99s else '-'} - {fmt(max(p99s)) if p99s else '-'} | {row['errors_total']:.0f} of {row['requests_total']:.0f} |"
            )
        self.w()
        dead = {r["target"]: r["dead"] for r in mem_rows if r["dead"]}
        if dead:
            self.w(f"WARNING: containers OOM-killed or restarted: {dead}")
            self.w()
        self.data["s9"] = out

    # ---------------------------------------------------------------- S10
    def s10(self):
        groups = []
        for target in ("litellm", "bifrost", "langfuse"):
            for profile in ("minimal", "prod"):
                rd = run_dirs(self.date_dir, target, profile, "s10")
                if rd:
                    groups.append((target, profile, rd))
        if not groups:
            return
        self.w("## S10 - maximum sustained rate (open loop, extended ladder)")
        self.w()
        self.w(
            "Each target climbs a ladder of constant arrival rates (30 s per step after a 10 s warm-up, non-streaming, mock `MOCK_LATENCY_MS=60`) past the point where it stops "
            "holding the rate. A step is sustained (D15 + D24) when the achieved rate is at least 95 % of the target, fewer than 0.1 % of requests fail, k6 uses under 4 cores, "
            "p50 is at most twice the direct p50 at that rate (S1 baselines; above 8000 RPS the 8000 RPS baseline is used, marked `~` in the JSON) and no container of the stack died. "
            "The result is the highest step before the first one that fails, per run; three runs on fresh stacks, median and range reported (D24). "
            "If the first failing step failed only because k6 itself was at its limit, or the ladder ended without a failure, the result is `at least` that rate (D17). "
            "Separate from the headline S2 numbers (contract rule 8)."
        )
        self.w()
        self.w("| target / profile | runs | highest sustained per run (RPS) | median | range | verdict | p50 / p99 ms at the median step | gateway CPU % | stack CPU % | k6 CPU % at that step |")
        self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        details = []
        out = {}
        for target, profile, rd in groups:
            per = []
            for r in rd:
                rows, highest, meta, dead = evaluate(r, self.base)
                first_fail = next((x for x in rows if not x.get("sustained")), None)
                at_least = first_fail is None or all(p.startswith("k6 bound") for p in first_fail.get("why", "").split("; "))
                per.append({"run": r, "rows": rows, "highest": highest, "at_least": at_least, "first_fail": first_fail, "dead": dead})
            highs = [p["highest"] or 0 for p in per]
            m = statistics.median(highs)
            lo, hi = min(highs), max(highs)
            al = any(p["at_least"] and (p["highest"] or 0) == m for p in per) or all(p["at_least"] for p in per)
            # numbers at the median step: median over the runs that sustained it
            at = [next((x for x in p["rows"] if x.get("rate") == m and not x.get("no_data")), None) for p in per]
            at = [x for x in at if x]
            gw = GATEWAY_CONTAINER[target].replace("gwbench-", "")
            if at:
                p50, p99 = statistics.median(x["p50"] for x in at), statistics.median(x["p99"] for x in at)
                gcpu = statistics.median(x["cpu"].get(gw, 0) for x in at)
                scpu = statistics.median(sum(v for k, v in x["cpu"].items() if k != "mock-llm") for x in at)
                k6 = statistics.median(x["k6_cpu"] for x in at)
                num = f"{p50:.1f} / {p99:.1f}"
            else:
                num, gcpu, scpu, k6 = "-", 0, 0, 0
            verdict = ("at least " if al else "") + f"{m:.0f}"
            self.w(
                f"| {target} / {profile} | {len(per)} | {', '.join(f'{h:.0f}' for h in highs)} | {m:.0f} | {lo:.0f} - {hi:.0f} | {verdict} | {num} | {gcpu:.0f} | {scpu:.0f} | {k6:.0f} |"
            )
            details.append((target, profile, per))
            out[f"{target}/{profile}"] = {
                "highest_per_run": highs,
                "median": m,
                "range": [lo, hi],
                "at_least": al,
                "dead": sorted({c["name"] for p in per for c in p["dead"]}),
                "runs": [p["run"] for p in per],
            }
        self.w()
        self.data["s10"] = out
        for target, profile, per in details:
            rates = []
            for p in per:
                for x in p["rows"]:
                    if x.get("rate") not in rates:
                        rates.append(x.get("rate"))
            rates.sort()
            self.w(f"**{target} / {profile}: every step** (median over the runs that reached it; `held` = runs in which the step passed all rules)")
            self.w()
            self.w("| step RPS | achieved | errors % | p50 ms | p95 ms | p99 ms | gateway CPU % | stack CPU % | k6 CPU % | held | why not (first failing run) |")
            self.w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            gw = GATEWAY_CONTAINER[target].replace("gwbench-", "")
            for rate in rates:
                xs = [x for p in per for x in p["rows"] if x.get("rate") == rate and not x.get("no_data")]
                if not xs:
                    continue
                held = sum(1 for x in xs if x.get("sustained"))
                why = [x["why"] for x in xs if x.get("why")]
                self.w(
                    f"| {rate:.0f} | {statistics.median(x['achieved'] for x in xs):.0f} | {statistics.median(x['error'] for x in xs) * 100:.2f} | {statistics.median(x['p50'] for x in xs):.1f} | "
                    f"{statistics.median(x['p95'] for x in xs):.1f} | {statistics.median(x['p99'] for x in xs):.1f} | {statistics.median(x['cpu'].get(gw, 0) for x in xs):.0f} | "
                    f"{statistics.median(sum(v for k, v in x['cpu'].items() if k != 'mock-llm') for x in xs):.0f} | {statistics.median(x['k6_cpu'] for x in xs):.0f} | {held} of {len(xs)} | {why[0] if why else ''} |"
                )
            self.w()

    # ---------------------------------------------------------------- whole report
    def header(self):
        self.w("# Results summary: S6 to S10 (story #10)")
        self.w()
        self.w(
            f"Generated by `scripts/summary_tables.py --set s6-s10` from `{self.date_dir}`. Primary numbers are k6 client-side distributions (D18); percentiles are never subtracted. "
            "Decisions: D28 (run plan and method). Everything ran on a quiet Docker VM with only `gwbench-*` containers (see each `metadata.json`)."
        )
        self.w()

    def run(self):
        self.header()
        notes = os.path.join(self.date_dir, "SUMMARY-S6-S10-notes.md")
        if os.path.exists(notes):
            with open(notes) as f:
                self.w(f.read().rstrip())
                self.w()
        self.s10()
        self.s9()
        self.s6()
        self.s7()
        self.s8()


def generate(date_dir, out=None):
    rep = Report(date_dir.rstrip("/"))
    rep.run()
    out = out or os.path.join(rep.date_dir, "SUMMARY-S6-S10.md")
    with open(out, "w") as f:
        f.write("\n".join(rep.lines) + "\n")
    jpath = os.path.splitext(out)[0] + ".json"
    with open(jpath, "w") as f:
        json.dump(rep.data, f, indent=1, default=str)
        f.write("\n")
    print(f"wrote {out} and {jpath}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date_dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    generate(a.date_dir, a.out)
