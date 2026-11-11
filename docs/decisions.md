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

## D13 - Bifrost: image, route, base URL, and what "minimal" means

**Image: `maximhq/bifrost:v2.5.1`**, index digest
`sha256:5ec9873acaf1687556b6e4dd854b92b539d9debf87a5a683b66338648b84d3c8`
(linux/arm64 manifest `sha256:e82c7d65bc8c996ef253d35d7fcebb723f13a1255278b0c5e5a173ac0d726aa1`,
image id `sha256:1a6229498bf2...`). Chosen on 2026-11-11: the newest non-prerelease
`transports/v2.5.1` release (published 2026-11-10, Docker Hub tag `v2.5.1`, also `latest`).
The issue text mentioned v1.6.4; the gateway has moved on to v2.x, so the config shapes
below are v2.5.1's. Multi-arch with a native arm64 manifest, nothing runs under
emulation. The digest is recorded in every run's `metadata.json`. v2.5.0 (arm64 too) is
the fallback.

**Route and model name.** k6 posts to `http://bifrost:8080/v1/chat/completions`, the
gateway's native OpenAI-format route, which is also contract rule 7's primary path. The
model must carry the provider prefix, `openai/mock-gpt`; a bare `mock-gpt` is refused
(HTTP 400 "could not auto resolve a provider"). The OpenAI drop-in integration routes
(`/openai/v1/chat/completions`, `/openai/chat/completions`) also work and accept bare
model names, but they go through a different handler and are not benchmarked.

**Provider base URL has no `/v1`.** Checked in the source
(`core/providers/openai/openai.go`: `BaseURL` is trimmed of trailing `/` and then
`/v1/chat/completions` is appended) and on a running stack: `base_url:
http://mock-llm:9000` reaches the mock's `/v1/chat/completions`, while
`http://mock-llm:9000/v1` makes Bifrost call `/v1/v1/chat/completions` and every request
fails with 404. The k6 base URL (`TARGET_BASE_URL`) does have `/v1`: that is Bifrost's own
route, not the provider's.

**`allow_private_network: true` on the provider.** Bifrost refuses to dial private
addresses (RFC 1918) by default, with "connection to private IP ... is not allowed", and
the mock's address on the Docker network is one. The per-provider `network_config`
switch opts in. Both profiles set it. Provider retries are off (`max_retries: 0`, as in
LiteLLM's `num_retries: 0`) so the mock is the only source of latency.

**Pricing datasheet is local, not downloaded.** Bifrost's model catalog downloads a
pricing datasheet from `getbifrost.ai` at start and exits with a fatal error if that
fails and no copy is stored; during this story the host could not reach the site
(timeouts), so the stack would not start at all. `framework.pricing` therefore points
at `file:///app/datasheet/pricing.json` and `model-parameters.json`
(`gateways/bifrost/datasheet/`, mounted read-only), each with one entry, `mock-gpt`, at
the same prices as LiteLLM (1 and 2 USD per million input and output tokens), and
`mcp_library_sync_interval: 0` stops the other startup download (prod, which has a config
store; the MCP library sync runs only there). Starts take about 6 seconds and need no
network. Difference from a real deployment: the catalog holds one model instead of
thousands, so lookups against it are as cheap as they can be.

**Minimal: config store OFF and log store OFF, both explicit.**

- `config_store: {enabled: false}` (file-only mode, no SQLite config database).
- `logs_store: {enabled: false}` and `client.enable_logging: false`. This is the part
  AIGatewayBench missed: with only `config_store` disabled, Bifrost still creates a SQLite
  log store (`logs.db`) in its data directory, starts the logging plugin and writes one row
  per request. Reproduced here: the same minimal config without the `logs_store` block and
  `enable_logging` gave `logs.db` and `logs.db-wal` (about 2 MB after 30 requests),
  `logging` and `telemetry` active, and a working `/api/logs`. The logging plugin loads when
  `enable_logging` is not false and a log store exists (`server/plugins.go`).
- The `telemetry` (Prometheus) plugin is disabled with a `plugins` entry (it is on by
  default). No semantic cache, OTel or Maxim plugin.
- Governance is not loaded: with no config store and no `governance` block the plugin
  fails its own init ("governance config is nil", logged once at error level) and
  `/v1` is open. `enforce_auth_on_inference: false` says so explicitly. The key k6 sends
  is ignored. What stays loaded: the compat and model-catalog-resolver plugins, which
  Bifrost always registers (they cannot be turned off in config). Those are part of
  what "Bifrost minimal" costs, like LiteLLM's built-in hooks.
- Log level `error` (`BIFROST_LOG_LEVEL`) in both profiles, so the proxy does not log
  per request.

**Verified how.** `gateways/bifrost/verify.py` (run by `verify.sh`, so by `smoke.sh` and
every `run.sh`), minimal: the mounted config says both stores are off; the data volume is
writable on purpose and holds only `config.json` before and after a burst of 30 requests
(no `*.db`, `*.db-wal`, `*.db-shm`, empty `logs/`, byte for byte unchanged); `/api/logs`
and `/api/logs/stats` answer 404 (the routes do not exist, not an empty list), `/metrics`
404; `/api/config` reports `enable_logging: false`; `/api/plugins` shows only
compat and model-catalog-resolver active. The check is not vacuous: against the flawed
config above it fails ten checks (log database appears, logs API 200, logging and
telemetry active).

**Tuning knobs, recorded at defaults for story #8.** Per provider, in the config:
`concurrency_and_buffer_size` = 1000 workers and a 5000-request queue (the code defaults
in `core/schemas/provider.go`; the docs say the same). Global, in `client`:
`initial_pool_size` = 5000 (the code default, `schemas.DefaultInitialPoolSize`; the JSON
schema's "300" is stale), `drop_excess_requests` = false. Go runtime: `GOGC` and
`GOMEMLIMIT` are unset (`BIFROST_GOGC`, `BIFROST_GOMEMLIMIT` pass them through);
GOMAXPROCS is set from the cgroup limit by the image (2). The docs' sizing rule is
concurrency = expected RPS and buffer = 1.5 x RPS, pool = 1.5 x total RPS, and
`GOMEMLIMIT` about 90% of the container limit (1800MiB here), `GOGC` 200 to 400.
`verify.sh` checks the three Bifrost values against the running process, so a changed
default shows up.

**Gateway-reported overhead.** Bifrost sends no overhead header. Its docs describe
`x-bifrost-upstream-latency-ms` (time blocked on the upstream, to three decimals) and say
to subtract it from the caller's own elapsed time to get what Bifrost cost. `k6/lib/chat.js`
does exactly that per request and records it as the Trend `gateway_overhead_ms`: k6's
`http_req_duration` for that request minus the header. It is a per-request difference, so it
is not the "subtracting percentiles" rule 3 forbids, but it is also not a number Bifrost
reports: it includes the k6 to Bifrost hop and k6's own timing. `summarize.py` labels it.
(Bifrost's log rows, prod only, carry its own `overhead_latency` per request; that
needs the log store, so it is no use for minimal.)

## D14 - Bifrost prod profile: SQLite stores, virtual key, setup token

**One container.** `config_store` and `logs_store` are both SQLite (`/app/data/config.db`,
`/app/data/logs.db`) on a compose volume, which is Bifrost's own default backend. The
gateway container (2 CPU / 2 GB) is the whole stack, so rule 1's stack total is
the gateway total: the SQLite work is inside that container and visible in its CPU. Postgres
for both stores is the other supported backend and would add a container; not done in
Phase 1 (it is a candidate for a later "prod with Postgres" row, as it is closer to what
LiteLLM's prod profile uses).

**What prod turns on.** Logging plugin on (content logging at the default, so request and
response bodies go into the log database, as a default deployment does); governance with
`enforce_auth_on_inference: true`, one virtual key `sk-bf-gwbench-virtual` (local dummy
value, sent as `Authorization: Bearer`, which works while dashboard auth is off) limited to
the `openai` provider and the model `mock-gpt`, with a 1,000,000 USD monthly budget (never
reached; it makes the budget check and cost accounting run on every request, like the
LiteLLM key); Prometheus telemetry left on (Bifrost's default, `/metrics`). Cost per request
comes from the local datasheet (D13), so the budget really is charged (81 micro-USD per
request: 1 prompt and 40 completion tokens).

**Model allow-list is `["mock-gpt"]`, not `["*"]`.** With `["*"]` the virtual key refuses
the model (HTTP 403 `model_blocked`): a wildcard is only honoured for models the model
catalog already maps to the provider, and the mock's name is not one of those. An exact
name matches without the catalog.

**Setup token.** Until dashboard auth is enabled, the OSS management API (`/api/*`,
`/metrics`) is locked (HTTP 403 "dashboard auth is not configured and no setup token is
set"). `setup_token` (dummy value `gwbench-setup-token`) unlocks it for requests that send it
in `x-bifrost-setup-token`; `verify.sh` uses that to read the logs API, budgets and
metrics. Inference does not need it. Enabling admin auth instead would make `Authorization`
carry the admin credential and push the virtual key to `x-bf-vk`, which k6 does not send.

**Port.** The overlays publish `127.0.0.1:18080` (`BIFROST_HOST_PORT`) for `verify.sh`:
the image has no curl and its busybox `wget` hides error bodies. k6 uses the Docker
network, not this port. It is the only target that publishes one.

**Log store cost is visible.** First short runs at 100 RPS (`results/2026-11-11/bifrost/`):
gateway container CPU 17% (minimal) vs 29% (prod) of one core, memory peak 83 MB vs 171 MB.

**No saturation on the ladder.** One short scratch run per profile over the whole ladder
(20 s per step, 5 s warm-up, not committed; the committed evidence is the 100 RPS run),
p50 of `http_req_duration` in ms against the S1 baseline of 62.4, 62.1, 61.9 and 61.9 at
the same rates (D9), every step at its target rate with 0 % errors:

| Rate | minimal p50 | minimal gateway CPU | prod p50 | prod gateway CPU |
| ---- | ----------- | ------------------- | -------- | ---------------- |
| 100 | 63.8 | 17 % | 64.5 | 30 % |
| 250 | 63.5 | 34 % | 63.7 | 52 % |
| 500 | 62.5 | 45 % | 62.5 | 60 % |
| 1000 | 62.1 | 58 % | 62.2 | 75 % |

Gateway CPU is the mean over the step, 100 % = one of its two cores. So the ladder
(100 to 1000) does not reach Bifrost's limit in either profile; S10 has to find it with
higher rates, and the k6 load generator (about 0.5 core at 1000 RPS) gets closer to its own
limit than the gateway does.
