# Benchmark contract

These rules apply to every run in this repo. A result that breaks a rule must
say so next to the number. Decisions that shaped the rules live in
[decisions.md](decisions.md).

## Fixed conventions

- Docker network `gwbench`. Mock hostname `mock-llm`, port `9000`.
- Host: Apple M1 (arm64), Docker Desktop with 8 CPUs and about 8 GB. All
  images must run on arm64.
- Gateway container limit: 2 CPU / 2 GB unless a story says otherwise.
- Only one gateway stack is up at a time.
- Every image is pinned by version tag. The digest is recorded in run metadata.

## Rules

1. **Equal resources, one gateway at a time.** Every gateway container gets the
   same CPU and memory limits, and only one gateway stack runs at a time. Also
   report the total CPU and memory of the gateway's whole stack: Langfuse Web
   and its stores, LiteLLM Postgres and Redis, the Bifrost log store.
2. **Same mock upstream.** All gateways call the same mock, with a fixed
   latency and the same response size. The direct-to-mock run (S1) is the
   baseline.
3. **Never subtract percentiles.** Percentiles do not subtract. Show the
   gateway and baseline distributions side by side. Use the gateway-reported
   overhead when available (LiteLLM `x-litellm-overhead-duration-ms`; Bifrost sends
   only `x-bifrost-upstream-latency-ms`, so its figure is each request's k6
   duration minus that header, see D13; the Langfuse gateway reports none in a header, see D23),
   and label it as gateway-reported.
4. **Two profiles per gateway, never mixed in one table.**
   - `minimal`: no database, no callbacks, no spend tracking, no log store.
     For Bifrost the log store is explicitly off.
   - `prod`: database, auth key, and logging or tracing on.
5. **One tuning pass per gateway.** Each gateway gets one documented tuning
   pass (decision D5). The tuned values are recorded per run.
6. **Verify the mode under test.** Before measuring, check that the mode is
   actually active: the profile's features are on or off as claimed, and the
   request goes through the gateway to the mock (for example by checking
   response headers, gateway or mock counters).
7. **Primary API path: OpenAI Chat Completions** (`/v1/chat/completions`).
   Other paths are secondary and labelled.
8. **Open-loop for headline numbers.** Headline numbers use a constant arrival
   rate (k6 `constant-arrival-rate`), and gateways are compared at equal
   rates. The maximum sustainable rate is reported separately (S10).
9. **The mock must not be the bottleneck.** The mock alone must sustain 2-3x
   the highest test rate. This is checked before gateway runs (story #4).
10. **Warm-up excluded, repeats, metadata.** Warm-up is excluded from the
    measurement window. Run 3 repeats where time allows and report the median
    and the spread. Record versions, image digests and host specs with every
    run.
11. **Docker Desktop noise.** Docker Desktop runs all containers in one VM, so
    load generator, mock and gateway share CPUs. If two identical runs differ
    by more than 5% at p50, say so in the results.

## Scenarios

| ID  | Scenario | What it measures | Notes |
| --- | -------- | ---------------- | ----- |
| S1  | Baseline direct | k6 straight to the mock, no gateway | The baseline for all other scenarios. Also the mock capacity check (rule 9). |
| S2  | Equal-rate steps | Latency at the same arrival rates through each gateway | Open-loop, rates stepped up, same steps for all gateways. Headline numbers. |
| S3  | Streaming | Time to first token and inter-chunk latency | SSE streaming through the gateway. |
| S4  | Concurrency sweep | Latency and throughput at rising concurrency | Closed-loop on purpose (fixed virtual users). Not a headline number. |
| S5  | Payload sweep | Overhead against request and response size | Same mock latency, varied payload sizes. |
| S6  | Logging and tracing on vs off | Cost of observability | Compares the `minimal` and `prod` style features on the same gateway. |
| S7  | Auth, virtual keys, rate limits | Cost of key checks and limits | Valid keys, many keys, limits configured but not hit. |
| S8  | Fault injection | Behaviour with slow, failing or dropped upstream responses | Uses the mock's fault knobs. Checks error handling and recovery. |
| S9  | Soak | Stability over a long run | Watches latency drift, memory growth and errors. |
| S10 | Max rate | Highest rate sustained with acceptable errors and latency | Reported separately from headline numbers (rule 8). |
