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
