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
