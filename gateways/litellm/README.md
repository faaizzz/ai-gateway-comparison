# LiteLLM target

LiteLLM proxy in front of the mock. Decisions: [D11 and D12](../../docs/decisions.md).
Contract for targets: [gateways/README.md](../README.md).

| | `minimal` | `prod` |
| --- | --- | --- |
| Containers | `gwbench-litellm` | `gwbench-litellm`, `-postgres`, `-redis`, `-setup` |
| Database, Redis | none | Postgres 17.6, Redis 8.2.2 |
| Spend tracking, spend logs | off | on (written to Postgres every 10 s) |
| Callbacks (logging, tracing) | none | none yet (hook for the Langfuse callback in S6) |
| Auth | master key (`sk-gwbench-master`) | virtual key (`sk-gwbench-virtual`), model `mock-gpt`, budget 1,000,000 USD |
| Gateway limits | 2 CPU / 2 GB | 2 CPU / 2 GB; Postgres 1 CPU / 1 GB; Redis 1 CPU / 512 MB; setup 0.25 CPU / 128 MB |

**Variants for S6 to S8 (D28)**, each a thin overlay on one of the two profiles above:

| Profile | Built from | Difference | Scenario |
| --- | --- | --- | --- |
| `prod-langfuse` | `prod` + `gateways/langfuse/compose.yml` | `success_callback: [langfuse]`; the Langfuse Web, worker and stores run as the tracing backend (its AI gateway and setup container are disabled); whole stack counted | S6 |
| `prod-rl` | `prod` | virtual key gets `rpm_limit`, `tpm_limit` and `max_parallel_requests` far above the load (`setup-key.py`) | S7 |
| `retry` | `minimal` | `num_retries: 2` in `litellm_settings` and `router_settings`, cooldowns off (one deployment) | S8 |

Image `ghcr.io/berriai/litellm:v1.109.0` (arm64, digest in D11). Server: Granian (`--run_granian`), workers `LITELLM_WORKERS`
(default 2), both from the tuning pass (D25). Log level: `LITELLM_LOG` (default `ERROR`). Model alias `mock-gpt`
(`openai/mock-upstream`, `api_base: http://mock-llm:9000/v1`). Base URL for k6:
`http://litellm:4000/v1`. No port is published; use `docker exec gwbench-litellm ...`
(the image has python, no curl).

```bash
scripts/smoke.sh litellm minimal        # up, plain + streaming request, verify.sh, down
scripts/smoke.sh litellm prod --keep    # leave it running for poking around
make run TARGET=litellm SCENARIO=s1 PROFILE=prod RATE=100 DURATION=20 WARMUP=5
LITELLM_WORKERS=4 make run TARGET=litellm SCENARIO=s1 PROFILE=minimal
```

Files: `target.env`, `docker-compose.minimal.yml`, `docker-compose.prod.yml`,
`config.minimal.yaml`, `config.prod.yaml`, `setup-key.py` (creates the virtual key),
`verify.sh` (checks that the profile is what it claims: no DB, Redis or logging
callbacks in minimal; DB, Redis, a key with a budget and spend rising in prod;
in both, a request answered by the mock with LiteLLM's headers, the worker count
and the image).

**Gateway-reported overhead.** Every response carries `x-litellm-overhead-duration-ms`.
k6 records it as `gateway_overhead_ms` and `scripts/summarize.py` prints it per step,
labelled as gateway-reported. It is LiteLLM's own measure and is not the same quantity
as the k6 latency; do not subtract either from the other.

**Gotchas**

- `test` inside the LiteLLM image is the Python venv's `test` script, not the shell
  builtin: use `CMD-SHELL` healthchecks with `[ ... ]`.
- `docker compose up --wait` fails if any container exits, so the setup container idles.
- LiteLLM's built-in hooks are always loaded; "no callbacks" means no logging or spend callback.
- Tuned (D25): on Granian with 2 workers the proxy holds 300 requests per second (minimal),
  against 250 on uvicorn. Above that it queues: the headline ladder (100 to 1000) goes past what
  LiteLLM can hold, and D15 marks those steps saturated. More workers do not help on 2 CPUs, and
  uvicorn cannot start 3 workers in 2 GB (OOM-killed).
