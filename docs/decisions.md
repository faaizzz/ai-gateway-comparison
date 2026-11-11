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

**Result (story #7): confirmed for the gateway, with a caveat for Web.** The gateway
blocks the mock without the allow-list (`blocked provider destination host="mock-llm"
error=provider address is not allowed`, HTTP 502) and serves it with
`LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST=mock-llm`, over plain http, with the cloud
region unset. From the source, Web uses the allow-list only when it validates a connection that is being
created or tested, not when it resolves a request (not tested with the Web side unset). But Web v4.78.0 refuses to create a connection
with a custom base URL at all outside development, preview and staging builds, so the
benchmark's connection is inserted into Postgres. Details in D22.

## D3 - Langfuse gateway calls Web per request

The Langfuse gateway calls Langfuse Web on every request to resolve its
configuration. Web caches this in Redis (60 s by default). The Langfuse stack
(Web, Redis and the other stores) therefore counts towards the total resources
reported under contract rule 1, and the cache TTL is recorded in run metadata.

**Result (story #7): confirmed, and the cache is narrower than this says.** One gateway
request is exactly one call to Web (`POST /api/internal/ai-gateway/v1/resolve`), with
the cache on or off; traces add one batched call per few hundred requests. The cache
saves Postgres work (about 2 transactions per request when off, none when on), not the
call, and Web still decrypts the credential and signs an ingestion token on every
resolve. Measurements in D23.

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

## D15 - Saturated steps: same ladder for everyone, marked, not compared

Every gateway runs the same ladder (D8), even when it cannot hold the top steps
(LiteLLM with 2 workers saturates between 100 and 250 requests per second, see D11).
The ladder is not capped per gateway, because a cap would hide where each gateway
breaks.

A step counts as **sustained** when all three hold:
- the achieved request rate is at least 95 % of the target rate,
- the error rate is below 0.1 %,
- the k6 load generator is not the limit (its CPU is recorded with the step).

Steps that are not sustained stay in the results and are marked **saturated**,
with their achieved rate and error rate. Their latency is not used in the
side-by-side latency tables: latency at saturation measures queueing in front of
the gateway, not the gateway's per-request cost. The report shows, for each
gateway and profile, the highest sustained ladder step next to the latency table.

## D16 - Tuning rule for story #8 (refines D5)

D5 said to pick the lowest p99 at 500 requests per second. That cannot rank
settings for a gateway that saturates below 500, so the rule is:

1. Fixed: 2 CPU / 2 GB per gateway container, `minimal` profile, mock at
   `MOCK_LATENCY_MS=60`. The `prod` profile reuses the chosen values.
2. For each candidate setting, find the **highest sustained rate** (D15) on the
   ladder, extended past 1000 (1500, 2000, 3000, ...) for gateways that hold 1000.
3. Pick the setting with the highest sustained rate. Break ties with the lowest
   p99 at the highest rate that every candidate sustains.
4. Short runs: 1 repeat, 30 s per step, 10 s warm-up, written to a scratch date
   and summarised in the decision entry (raw scratch folders are not committed).

Tuning stays within each gateway's documented knobs (LiteLLM workers, Bifrost
concurrency/buffer/pool and Go GC settings, the Langfuse gateway's memory budget and
documented performance settings). The gateway container limit is never changed.

## D17 - S10 max rate

S10 extends the ladder per gateway (1000, 1500, 2000, 3000, 4000, 6000, 8000) and
stops at the first step that is not sustained (D15). The result is the highest
sustained rate, with p50/p99 and gateway CPU and memory at that step. If k6 uses more
than 4 cores at a step, the step is marked load-generator bound and the result is
reported as "at least" that rate. S10 numbers are reported separately from the
equal-rate headline tables (contract rule 8).

## D18 - Overhead numbers: primary and secondary

- **Primary:** the client-side (k6) latency distributions of the gateway and the
  direct baseline at the same rate, side by side (p50, p95, p99), per contract
  rule 3. This is the only number compared across gateways.
- **Secondary:** gateway-reported overhead, in its own labelled column with its
  definition. The definitions differ: LiteLLM reports time inside the proxy
  (including its own queueing), and Bifrost's figure is the k6 duration minus
  `x-bifrost-upstream-latency-ms`, so it includes the network hops. Gateway-reported
  numbers are never ranked against each other.

## D19 - Price parity for the mock model

Every gateway is given the same price for the mock model in local config:
1 USD per million input tokens and 2 USD per million output tokens. Prices are not
fetched from the internet at runtime. This makes every `prod` profile do the same
cost arithmetic, and keeps runs independent of external sites. (Bifrost exits at
start if its online pricing download fails, so it uses a local datasheet, D13.)

## D20 - Run hygiene and local checks

- **Quiet Docker VM.** Measured runs happen with only `gwbench-*` containers
  running. Unrelated containers are stopped beforehand (the owner approved stopping
  an unrelated compose project on 2026-11-11), and run metadata records any other
  running containers so a noisy run can be spotted.
- **Scratch runs stay out of committed results.** Short and scratch runs go to a
  scratch date (`scripts/run.sh --date <name>`) and are deleted. Issue #19 fixes
  `make run` ignoring `DATE=` and records non-`gwbench` containers in metadata.
- **Checks run locally.** GitHub Actions was turned off by the owner on 2026-11-11.
  Before each push: `make lint`, `make base-smoke`, and `scripts/smoke.sh <gateway>
  <profile>` for each gateway touched. The workflow file stays in the repo, so CI can
  be turned on again with `gh workflow enable ci`.
- **Latest stable versions.** Each gateway uses its latest stable release on the
  day it was added (so Bifrost v2.5.1, not the v1.6.4 that AIGatewayBench used).
  Comparisons with published benchmarks name both versions.

## D21 - Langfuse stack: versions, services, limits, setup

**Versions: Langfuse v4.78.0 for all three Langfuse images** (the issue text said "v3";
the gateway only works with Web newer than 4.75.0, see the official compose file, so the
v4 line is the one to run). Chosen on 2026-11-11 as the newest release (published
2026-11-10); the AI gateway is in public beta since 2026-11-04. All multi-arch with a
native arm64 manifest, nothing runs under emulation. Digests of the multi-arch indexes,
recorded in every run's `metadata.json`:

| Image | Digest |
| --- | --- |
| `langfuse/ai-gateway:4.78.0` | `sha256:a9d9af37968a2ff9dcdbc614fef9702fc6126a586abc1d6a869b326efa1c447f` |
| `langfuse/langfuse:4.78.0` (Web) | `sha256:037188385ac6612d3494319fa060052da407b235502874c7ea79ade583c29002` |
| `langfuse/langfuse-worker:4.78.0` | `sha256:175a32200b9908b283104e67b84d080381e841daa8ee6692f5bb26538df1a59e` |
| `postgres:17.6-alpine3.22` | `sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94` |
| `clickhouse/clickhouse-server:26.3.62.3` (the tag of the official compose file) | `sha256:de3e47c64078aa196dae2349e048fc98ba81947f651f5c20e06e46baea8ae40a` |
| `redis:8.2.2-alpine3.22` (the official file uses 7; same image as LiteLLM's Redis) | `sha256:59b6e694653476de2c992937ebe1c64182af4728e54bb49e9b7a6c26614d8933` |
| `cgr.dev/chainguard/minio` (MinIO, as in the official file) | `sha256:fb893e36fc369d41e92f678b17ec27378fa5bf0576aeef7ac15460c13629ab5a` |

MinIO has no versioned free tag (Chainguard publishes `:latest` only; the Docker Hub and
Quay MinIO images are gone), so it is pinned by digest. The fallbacks are v4.77.0 and
v4.76.0 (both with arm64 gateway images).

**Services and limits** (`gateways/langfuse/compose.yml`, one file for both profiles):
gateway 2 CPU / 2 GB; Web 2 CPU / 1.25 GB; worker 1 CPU / 1 GB; Postgres 1 CPU / 512 MB;
ClickHouse 2 CPU / 1.5 GB; Redis 1 CPU / 256 MB; MinIO 1 CPU / 384 MB; setup 0.5 CPU /
256 MB. Limits add up to 6.9 GB on paper; measured use is far lower (D23). The sum
fits in the 8 GB Docker VM with the mock (512 MB limit, 2 MB used) and k6. ClickHouse is
the one that comes close to its limit (1.2 GB peak in prod at 1000 RPS), so it gets
`clickhouse-low-memory.xml` (small mark cache, no uncompressed cache, no system log tables
except `query_log`, which the worker reads). Web and worker get `--max-old-space-size`
below their limits. Web and the worker bind to the container hostname, so their
healthchecks use it, and the worker waits for Web because Web applies the Postgres and
ClickHouse migrations.

**Profiles are an ingestion mode, not a different stack.** The gateway cannot work without
Web, so `minimal` cannot drop Langfuse. `minimal` = gateway ingestion mode **NONE**
(nothing is sent to Langfuse, no trace header), `prod` = **USAGE** (the default: a generation
with token usage per request, no prompt or response), and `LANGFUSE_INGESTION_MODE=FULL`
(prompts and responses too) is the variant for S6. The mode is organisation config stored in
Postgres (not an environment variable), written by `langfuse-setup`; `target.env` exports it
per profile. `verify.sh` reads it back from Postgres and proves the effect (no trace header
and nothing in ClickHouse in NONE; header and all generations with usage and no content in
USAGE; content too in FULL). Both profiles use the same gateway key, so key resolution is
equal in both.

**Headless setup (`gateways/langfuse/setup.mjs`).** `LANGFUSE_INIT_*` on Web creates the
organisation, project, project keys and owner user. A setup container (the Web image, which has
Node and Prisma) logs in as that user and calls the tRPC routes of the gateway UI:
`aiGateway.updateConfig` (ingestion project and mode) and `aiGateway.createApiKey` (gateway key),
and tries `aiGateway.createConnection`. It also defines the mock's model price in the Langfuse
project through the public API (`POST /api/public/models`, 1 and 2 USD per million input and
output tokens, D19), so prod generations are costed like the other gateways' requests; `verify.sh`
checks one generation at 81 micro-USD. Two parts are not clean API use:
(1) the connection falls back to a Prisma insert (D22); (2) Web generates the key secret
itself, so the script replaces the new key's stored hash with the hash of a fixed dummy secret
(`sk-lf-gwbench-gateway-key`, same salted SHA-256 as Web's `createShaHash`), so k6 uses a
constant key like the other targets. The ES256 key that signs ingestion tokens is generated in
memory inside the Web container at every start, so no private key is committed or ends up in
`compose.resolved.yml`. All other keys and passwords are local dummy values.

**Gateway settings.** `LANGFUSE_AI_GATEWAY_WEB_URL=http://langfuse-web:3000` with
`LANGFUSE_AI_GATEWAY_ALLOW_HTTP_WEB_URL=true` (the gateway warns at start that traffic is
unencrypted; it stays on the Docker network), the shared service key, the allow-list, log level
`warn` (no line per request, like LiteLLM and Bifrost). Memory budget and telemetry buffer are
at their defaults (60 % of the cgroup limit minus 64 MiB; 64 MiB). k6 uses the native route
`POST /openai/v1/chat/completions` with model `gpt-mock` (Web routes `gpt-*` to the OpenAI
connection and the gateway forwards the name unchanged).

## D22 - Langfuse go/no-go: GO, with a seeded connection (D2)

**Outcome: go.** The gateway reaches the mock at `http://mock-llm:9000/v1` over plain http
through its custom-base-URL support, so the TLS fallback (network alias `api.openai.com`,
self-signed terminator, `SSL_CERT_FILE`) was not needed and not built. Plain and streaming
requests work, the rate ladder (100 to 1000 RPS) runs, and a bad key gets 401.

**Caveat found in the source and on the running stack.** Web v4.78.0 gates the control-plane
write: `createConnection` with a `baseURL` fails with "Custom base URLs and extra headers are
not available yet" unless `isGatewayPreReleaseEnvironment()` is true, which needs
`NODE_ENV=development`, a PR-preview build (`NEXT_PUBLIC_PREVIEW_PR_URL`) or Langfuse Cloud
region STAGING or DEV. A normal self-hosted production image is none of these (the Web image bakes
in an empty `NEXT_PUBLIC_LANGFUSE_CLOUD_REGION`, which counts as unset). The gateway's README
and the data plane (resolve, outbound checks) have no such gate, which is why a row inserted into
`gateway_ai_connections` works. `setup.mjs` therefore tries the tRPC route first, logs the
refusal, and inserts the row with Prisma using Web's own encryption and column layout. It is
the "seed against Postgres" last resort the issue allows, and it means two things for the
report: the benchmark exercises a setup that a stock self-hosted user cannot create from the
UI today, and the allow-list (D2) is not enough on its own. I did not try to flip the gate with
an environment variable (`NEXT_PUBLIC_*` values are inlined when the image is built, so a
runtime setting very likely does nothing); nothing in the benchmark depends on it.

The path measured is equal to the other gateways on the wire: plain http to the mock, no
TLS terminator hop, no extra proxy.

## D23 - Langfuse: Web calls per request (D3), resources, ladder, overhead

**Web calls per gateway request (D3).** `gateways/langfuse/d3.py` puts a counting proxy
(`d3_proxy.py`) between the gateway and Web, sends 200 requests (4 threads, USAGE mode,
after 5 warm-up requests) and reads the proxy plus Postgres and Redis counters (minus the idle
background rate of the worker's queues):

| Resolve cache | `POST /resolve` per request | `POST /otel/v1/traces` per request | Postgres transactions per request | Redis commands per request |
| --- | --- | --- | --- | --- |
| on (default, TTL 60 s) | 1.000 (200 for 200) | 0.005 (1 batch for 200) | about 0 | 6.1 |
| off | 1.000 (200 for 200) | 0.005 (1 batch for 200) | 2.1 | 5.6 |

So the gateway really calls Web once per request, and the Redis cache does not change that: it
removes the Postgres key and connection lookup (about 2 transactions), nothing else. What
still runs in Web per request regardless (from the source): the HMAC check, the rate-limit counters
in Redis (the 5 to 6 Redis commands per request in the table are most likely these; not measured
separately), decrypting the credential and signing an ES256 ingestion token. Web looks like the
limit of the stack (below). Trace uploads are batched, so they
cost almost nothing per request. The cache TTL and switch are in the Web container's
environment and in `compose.resolved.yml` of every run.

**Resources of the whole stack** (contract rule 1; `resources.csv` of the committed runs,
`results/2026-11-11/langfuse/{minimal,prod}/s1/run-1/`, 100 RPS):

| | idle (after setup) | minimal at 100 RPS | prod at 100 RPS |
| --- | --- | --- | --- |
| Gateway container | 3 MB, 0 % CPU | 7 % CPU, 5 MB peak | 8 % CPU, 18 MB peak |
| Whole Langfuse stack, CPU (100 % = 1 core) | about 13 % | 48 % | 54 % |
| Whole Langfuse stack, memory (sum of container peaks) | about 2.3 GB | 2.2 GB | 2.3 GB |

Per container at 100 RPS (CPU mean over the step, memory peak): Web 28 to 29 % and 0.85 to 0.87 GB,
ClickHouse 4 % and 0.76 to 0.86 GB, worker 4 % (minimal) and 10 % (prod) and 0.37 to 0.39 GB,
MinIO 1 % and 65 to 85 MB, Postgres 1 % and 50 MB, Redis 2 % and 13 MB. The gateway itself is
the smallest part of the stack. At 1000 RPS in prod the peaks are 3.3 GB in total (ClickHouse 1.18,
Web 0.98, worker 0.53, gateway 0.33 GB) and no container is near its limit except ClickHouse
(1.5 GB), so the stack fits the 8 GB VM with the mock and k6.

**Latency, short runs** (20 s per step after 5 s warm-up, one run each, so indicative; S1 direct at
the same rates is 62.4, 62.1, 61.9 and 61.9 ms, D9). p50 of `http_req_duration` in ms with p99 in
parentheses, Web CPU as % of one core over the step. The 100 RPS runs are the committed evidence
(minimal 64.8 / 72.2, prod 64.7 / 70.6); the rows below are one scratch ladder per profile, not
committed (same practice as D14):

| Rate | minimal p50 (p99) | Web CPU | prod p50 (p99) | Web CPU |
| --- | --- | --- | --- | --- |
| 100 | 65.0 (77) | 34 % | 65.0 (81) | 34 % |
| 250 | 63.8 (81) | 48 % | 63.9 (137) | 55 % |
| 500 | 63.0 (150) | 67 % | 65.4 (561), 494 achieved | 101 % |
| 1000 | 63.3 (245), 992 achieved | 104 % | 208.7 (3080), 911 achieved, 0.58 % errors | 138 % |

The p50 is 2 to 3 ms above the baseline while the stack keeps up. The tail goes first: p99 passes
100 ms at 250 RPS (prod) and 500 RPS (minimal). The prod ladder was run twice (the first before
the model price was added to the setup, D19): then 500 RPS held with p99 204 ms and at 1000 the
stack reached 960 RPS with p50 148 ms and p99 1180 ms. So prod saturates somewhere between 250
and 1000 RPS and the exact point moves between runs (the second run, with cost calculation on
the worker, is worse at 500); minimal keeps its p50 to 1000 RPS with a tail that is already
wide (p95 189 ms) and 8 RPS short. The limit looks like Web: it is one Node process whose CPU
reading reaches 100 % at 500 to 1000 RPS, while the gateway container uses at most 53 % of its
2 CPUs and the other containers are far from theirs. This is an inference from CPU readings, not a
profile. By the D15 rule the first prod run's 1000 step counts as sustained on its rate alone (96 %
of the target, above the 95 % line, no errors) although p50 is 2.4 times the baseline: the rule has
no latency criterion, which story #8 may want. For story #8: a Web setting (more CPU or a second Web
replica, `NODE_OPTIONS`) is where tuning would matter, repeats are needed because of the variation
between runs, and the ladder is meaningful here up to 1000.

**No overhead header.** The gateway's response headers are `langfuse-request-id`,
`langfuse-trace-id`, `langfuse-observation-id`, `langfuse-organization-id`, `langfuse-project-id`,
`langfuse-provider` and `langfuse-provider-connection-id`; none carries a duration. The gateway has
an OTLP metric `gateway.overhead.duration` ("time to response headers minus the caller's upload and
the provider's wait"), which needs a collector and is not wired up. So `gateway_overhead_ms` stays
empty for Langfuse, no change to `k6/lib/chat.js` or `summarize.py`, and its numbers are compared
through the k6 latency and the S1 baseline only. A future story can add a collector and read that
metric (OTLP endpoint `OTEL_EXPORTER_OTLP_ENDPOINT`), labelled as gateway-reported.

**Traces (for S6).** USAGE traces appear in the project's v2 observations API about 3 s after the
last request (35 of 35 in the smoke and verify bursts, FULL as well); they are best effort and
not durable, so S6 must count them against requests sent under load and report lag and loss.

## D24 - Sustained steps need a latency limit and a second run (amends D15, D16)

Story #7 found two gaps in D15. Langfuse prod at 1000 requests per second reached 96 %
of the target with no errors, so it counted as sustained, yet its p50 was 148 ms
against a 62 ms baseline. And two ladder runs of the same profile disagreed at 500.

1. **Latency limit.** A step is sustained only if, in addition to D15's rules, its
   p50 is at most **2x the direct baseline p50 at the same rate** (about 125 ms with
   the 60 ms mock). Above that the step is queueing, not adding a per-request cost.
   The limit is the same for every gateway and is checked against S1 results.
2. **Confirm the boundary.** A gateway's highest sustained step (in tuning, D16, and
   in S10, D17) must hold in two runs out of two. If the runs disagree, the lower
   step is reported and the disagreement is noted.
3. **S10 repeats.** S10 runs 3 times per gateway and profile and reports the median
   highest sustained rate, with the range.
