# Decisions

Short records of benchmark decisions. Rules that follow from them live in
[benchmark-contract.md](benchmark-contract.md).

## D1 - Reuse the mock upstream from AIGatewayBench

Reuse `crates/mock-upstream` from
[BerriAI/ai-gateway-bench](https://github.com/BerriAI/ai-gateway-bench) (MIT)
instead of writing a new mock. It is deterministic, fast and already used for
gateway benchmarks. Story #2 vendors it with its licence notice, a Dockerfile
and fault knobs.

## D2 - Langfuse allow-list for the mock host

The Langfuse AI Gateway only calls hosts on an allow-list. Set
`LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST` on both the gateway and the
Langfuse Web container so the mock host (`mock-llm`) is accepted. Plain http is
allowed unless `NEXT_PUBLIC_LANGFUSE_CLOUD_REGION` is set, so that variable must
stay unset.

## D3 - Langfuse gateway calls Web per request

The Langfuse gateway calls Langfuse Web on every request to resolve its
configuration. Web caches this in Redis (60 s by default). The Langfuse stack
(Web, Redis and the other stores) therefore counts towards the total resources
reported under contract rule 1, and the cache TTL is recorded in run metadata.

## D4 - LiteLLM Rust mode deferred

LiteLLM's Rust mode is not benchmarked in Phase 1: it has no coverage for the
OpenAI provider and no published image. Revisit when both exist.

## D5 - One tuning pass per gateway

Each gateway gets one documented tuning pass (story #8) using its own
guidance. The tuned values are recorded with every run. No further per-scenario
tuning, so no gateway gets an unequal amount of effort.

## D6 - Resource sampling: Docker socket stats for CSV, cAdvisor for Grafana

Tried cAdvisor (v0.60.4) first on Docker Desktop for Mac. It does give
per-container CPU and memory, but only with `privileged: true` and the Docker
socket mounted; without the socket it reports only raw cgroup ids with no
container names. It is also an extra privileged container that burns CPU in the
same VM as the system under test, and it needs Prometheus to get a CSV out.

So the per-run CSV comes from `scripts/sampler.py` instead: a small stdlib
Python script that reads the Docker Engine stats stream over the socket, one
sample per second per container, and writes `ts, container, cpu_percent,
mem_bytes, ...`. It runs only during a run, needs no other service, and gives
exact 1 s spacing (on Docker Desktop a polled one-shot stats call blocks for
about a second, so the stream is used instead). cAdvisor is kept for the
optional `obs` profile, where Grafana needs a Prometheus source. CPU is
reported as 100 = one core, memory as working set (usage minus inactive file
cache), both like `docker stats`.

## D7 - Custom k6 build with xk6-sse, pinned

`k6/Dockerfile` builds k6 v2.3.0 with xk6-sse v0.2.0 using the `grafana/xk6`
v1.4.15 builder and ships it on the stock `grafana/k6:2.3.0` runtime image.
xk6-sse v0.2.0 targets k6 v2, so k6 must be v2.x. The extension is compiled in:
nothing is downloaded when a test runs (k6's automatic extension resolution is
not used). The image is tagged `gwbench/k6:2.3.0-sse0.2.0`. xk6-sse emits no
time-to-first-token metric and no `http_req_waiting`, so scripts time the first
`data:` event themselves (see `k6/smoke.js`).

## D8 - Phase 1 rate ladder and mock capacity

**Ladder: 100, 250, 500, 1000 requests per second** (`k6/lib/ladder.js`, used by
S1 and later by S2 for every gateway). Non-streaming, mock at
`MOCK_LATENCY_MS=60`.

**Mock ceiling: not reached, at least 20,000 requests per second.** The S1 ramp
(`make run TARGET=direct SCENARIO=s1-ramp`, k6 on the same Docker Desktop VM, 8
CPUs, 15 s per step after a 10 s warm-up) went 1000, 2000, 4000, 8000, 12000,
16000, 20000 and every step held its target rate with 0 % errors. p50 stayed at
61.3-61.7 ms (the mock's 60 ms plus about 1.5 ms). p99 stayed at 64-68 ms up to
8000 and rose to 78 ms at 16000 and 80 ms at 20000; 115 iterations were dropped
over the whole run, all at the top steps. At 20000 requests per second k6 used
about 4.3 cores and the mock about 1 of its 2 cores, so the limit of this setup
is the load generator and the VM, not the mock. The ramp stops there because
nothing a gateway on 2 CPUs does gets near it. Result set:
`results/2026-11-11/direct/none/s1-ramp/run-1/`.

So the highest ladder rate (1000) is at most 1/20 of the ceiling, well inside the
1/2 to 1/3 required by contract rule 9. The ladder is chosen by what the
gateways can do, not by the mock: 1000 is above what LiteLLM is expected to hold
on 2 CPUs (S10 finds the real maximum) and low enough that k6 itself costs
under one core (74 % of a core at 1000). Rates above 1000 are for S10 only.

**Durations.** 15 s warm-up at the first rate, then 60 s per step, plus a 1 s
rate change between steps. Both are excluded from results: every iteration tags
its metrics with the step it started in, and only the `r<rate>` sub-metrics in
`summary.json` count (see `k6/lib/arrival.js`). One S1 run is 4 x 61 s + 16 s
= 4.5 minutes plus about 40 s of stack start and teardown. A gateway scenario
with 4 steps and 3 repeats on a fresh stack each is therefore about 17 minutes per
profile. Each repeat starts a fresh stack on purpose (no state carried over);
the cost is the gateway start time per repeat.

## D9 - S1 repeatability

Three S1 runs, each on a fresh stack, no other load on the Docker VM
(`results/2026-11-11/direct/none/s1/run-1..3`). p50 of `http_req_duration` in ms:

| Rate | run 1 | run 2 | run 3 | spread |
| ---- | ----- | ----- | ----- | ------ |
| 100 | 62.37 | 62.28 | 62.38 | 0.1 % |
| 250 | 62.09 | 62.04 | 62.22 | 0.3 % |
| 500 | 61.91 | 61.90 | 61.91 | 0.0 % |
| 1000 | 61.94 | 61.72 | 61.99 | 0.4 % |

The p50 differs by at most 0.4 % between runs, far under the 5 % noise
threshold of contract rule 11. p99 is less steady (64-68 ms; at 1000 it was 67.8,
67.6 and 66.7) and max varies from 75 to 107 ms, so tail numbers need the
median of repeats and the spread. Achieved rate matched the target to within
0.1 % in every step and there were no errors.

An earlier set of runs was discarded: other containers in the same Docker VM
(another project) were using about 8 % CPU and 1.4 GB. Before gateway runs, check
`docker ps` for anything else running.

## D10 - S1 results use per-step sub-metrics, not whole-run totals

A run is one k6 scenario that walks through warm-up and the steps, so the
whole-run totals in `summary.json` (and the k6 console summary) mix warm-up,
rate changes and every step. Read only `http_req_duration{step:r<rate>}` and the
other `{step:...}` sub-metrics, or run `scripts/summarize.py <run-dir>`.
The sub-metrics exist because each measured step has a threshold that always
passes. `dropped_iterations` cannot be split per step (a dropped iteration never
starts), so the table uses the achieved request rate per step to show saturation.
The k6 tag for the benchmark scenario is `bench_scenario`, because k6 already
uses `scenario` for its executor name.

## D11 - LiteLLM: image, workers, what "minimal" means

**Image: `ghcr.io/berriai/litellm:v1.109.0`**, index digest
`sha256:26aecfb02fb17ac6590f0549478561f9fbb3f69314317320b2e32a21beed4006`
(linux/arm64 manifest `sha256:c46e66a77104a6e2f006cdd76b30becc49a5c60647f98f8ced30d25d6fbf53d7`,
image id `sha256:6f8516908206...`). Chosen on 2026-11-11 as the newest non-prerelease
GitHub release (v1.109.0, published the same day; v1.110.0 was still `-rc.1`). It is a
multi-arch image with a native arm64 manifest, so nothing runs under emulation. The
plain `litellm` image is used for both profiles (the separate `litellm-database`
image is not needed: the plain image connects to Postgres and applies its schema when
`DATABASE_URL` is set, as the prod smoke test shows). v1.108.5 (arm64 too) is
the fallback if v1.109.0 turns out to have a regression; the digest is recorded in
every run's `metadata.json`.

**Workers: 2, from `LITELLM_WORKERS`** (`--num_workers`), matching the 2 CPUs of the
gateway container. Not tuned: story #8 sets the final value and records it. Python
workers are separate processes, so one worker cannot use two cores. `verify.sh`
counts the worker processes to confirm the setting took effect.

**Model alias.** `mock-gpt` maps to `openai/mock-upstream` with
`api_base: http://mock-llm:9000/v1`, and per-token prices in `model_info` (1 and 2
USD per million input and output tokens). The mock is not in LiteLLM's price
list, so without prices every cost would be 0 and spend tracking would have nothing
to show. Both profiles have the same prices, so both do the same cost arithmetic.

**What minimal turns off.** No `DATABASE_URL` (so no key store, no spend logs, no
UI), no Redis, `callbacks`/`success_callback`/`failure_callback` empty,
`disable_spend_logs: true`, `turn_off_message_logging: true`, no response cache,
`num_retries: 0` (both profiles: the mock should be the only source of latency).
Authentication is the master key only. LiteLLM still loads its own built-in hooks
(parallel-request limiter, budget limiter, cache control check and so on; about ten
classes in `/active/callbacks`) with or without a database. They cannot be
switched off by config and are part of what "LiteLLM minimal" costs. `verify.sh`
therefore checks for the absence of logging, tracing and spend callbacks (Langfuse,
OTel, Prometheus, `_ProxyDBLogger`), not for an empty list.

**Log level.** `LITELLM_LOG=ERROR` in both profiles, so the proxy does not write a
line per request. Recorded in `compose.resolved.yml`.

**Gateway-reported overhead.** `x-litellm-overhead-duration-ms` is present on every
response in both profiles. k6 records it as the Trend `gateway_overhead_ms`
(`k6/lib/chat.js`), per step like the other metrics, and `scripts/summarize.py`
prints it under its own heading as gateway-reported (contract rule 3). It measures
time spent inside LiteLLM, so under overload it includes queueing in the proxy and
grows with load (it is not a constant per-request cost).

## D12 - LiteLLM prod profile: virtual key, Redis, setup container

**Services (stack total counted under contract rule 1):** `litellm` 2 CPU / 2 GB,
`litellm-postgres` (postgres:17.6-alpine3.22) 1 CPU / 1 GB, `litellm-redis`
(redis:8.2.2-alpine3.22, no persistence) 1 CPU / 512 MB, `litellm-setup` 0.25 CPU /
128 MB. Stack limits are 4.25 CPU / 3.6 GB; what is actually used is in
`resources.csv`.

**Virtual key.** The `litellm-setup` container calls `/key/generate` with the master
key and a fixed dummy value (`sk-gwbench-virtual`), so k6 can use it without reading
it back. The key is limited to the `mock-gpt` model, has `max_budget` 1,000,000 USD
(never reached: it exists so the budget check runs on every request, S7 changes the
budget) and a 30-day budget duration. k6 uses this key in the prod profile; the
master key is used only for admin calls.

**Setup container idles instead of exiting.** `docker compose up --wait` treats an
exited container as a failure, even with exit code 0, and the runner needs `up
--wait`. So the setup container creates the key, writes a ready file and then
sleeps; its healthcheck goes green once the key exists, and the proxy's own
healthcheck gates the setup, so the key exists before the smoke request. It reuses
the LiteLLM image (it has python), so no extra image is pulled.

**Spend tracking is on** (`disable_spend_logs: false`, `_ProxyDBLogger` loaded).
Spend is written to Postgres in batches (`proxy_batch_write_at: 10` seconds, the
default is 60) so `verify.sh` can see it within a short run; key spend and spend logs
are what it checks. With Redis configured LiteLLM also keeps its spend and
rate-limit counters there (`spend:key:*`, `{api_key:...}:tokens`), which is the part
of Redis a request touches. Redis is not a response cache (`cache: false`): every
request still reaches the mock.

**Hook for S6 (Langfuse).** `config.prod.yaml` has empty `callbacks`,
`success_callback` and `failure_callback`; the Langfuse callback and its keys go
there and in the compose overlay's `litellm.environment`.
