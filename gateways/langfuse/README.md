# Langfuse AI Gateway target

Self-hosted Langfuse plus `langfuse/ai-gateway` in front of the mock. Decisions:
[D15 to D17](../../docs/decisions.md) (D2 and D3 are the go/no-go questions).
Contract for targets: [gateways/README.md](../README.md).

| | `minimal` | `prod` |
| --- | --- | --- |
| Containers | the whole stack, same as prod | the whole stack |
| Gateway ingestion mode | **NONE**: no generation is sent to Langfuse | **USAGE**: one generation per request with token usage, no prompt or response |
| S6 variant | | `LANGFUSE_INGESTION_MODE=FULL`: prompt and response recorded too |
| Auth | gateway key `sk-lf-gwbench-gateway-key` (resolved by Web on every request) | same |
| Gateway limit | 2 CPU / 2 GB | 2 CPU / 2 GB |

The two profiles run the same containers. Langfuse Web has to be up in both because the
gateway asks it to resolve every request (the control plane); what the profiles switch is
whether the gateway sends traces. So "minimal" here means "no tracing", not "no Langfuse".
Contract rule 1 applies: report the whole stack, not only the gateway container.

Images (all arm64, versions pinned, digests in D15 and in every run's `metadata.json`):

| Service | Container | Image | Limit |
| --- | --- | --- | --- |
| `langfuse-ai-gateway` | `gwbench-langfuse-ai-gateway` | `langfuse/ai-gateway:4.78.0` | 2 CPU / 2 GB |
| `langfuse-web` | `gwbench-langfuse-web` | `langfuse/langfuse:4.78.0` | 2 CPU / 1.25 GB |
| `langfuse-worker` | `gwbench-langfuse-worker` | `langfuse/langfuse-worker:4.78.0` | 1 CPU / 1 GB |
| `langfuse-postgres` | `gwbench-langfuse-postgres` | `postgres:17.6-alpine3.22` | 1 CPU / 512 MB |
| `langfuse-clickhouse` | `gwbench-langfuse-clickhouse` | `clickhouse/clickhouse-server:26.3.62.3` | 2 CPU / 1.5 GB |
| `langfuse-redis` | `gwbench-langfuse-redis` | `redis:8.2.2-alpine3.22` | 1 CPU / 256 MB |
| `langfuse-minio` | `gwbench-langfuse-minio` | `cgr.dev/chainguard/minio` (by digest) | 1 CPU / 384 MB |
| `langfuse-setup` | `gwbench-langfuse-setup` | `langfuse/langfuse:4.78.0` | 0.5 CPU / 256 MB |

k6 base URL `http://langfuse-ai-gateway:8080/openai/v1`, route `POST /openai/v1/chat/completions`
(the gateway's native OpenAI route; contract rule 7), model `gpt-mock` (Web routes `gpt-*`
to the OpenAI connection; the mock ignores the name). Loopback ports: gateway
`127.0.0.1:18081` and Web `127.0.0.1:13030` (for `verify.sh`; the UI login is
`gwbench@example.com` / `gwbench-local-password`).

```bash
scripts/smoke.sh langfuse minimal         # up (about 1 to 2 min), plain + streaming request, verify.sh, down
scripts/smoke.sh langfuse prod --keep     # leave it running for poking around
make run TARGET=langfuse SCENARIO=s1 PROFILE=prod RATE=100 DURATION=20 WARMUP=5
LANGFUSE_INGESTION_MODE=FULL scripts/smoke.sh langfuse prod
LANGFUSE_AI_GATEWAY_CACHE_RESOLVE_ENABLED=false scripts/smoke.sh langfuse prod
python3 gateways/langfuse/d3.py           # D3: Web calls per gateway request, cache on and off
```

Files: `target.env`, `compose.yml` (one file, both profiles), `setup.mjs` (headless setup),
`clickhouse-low-memory.xml`, `verify.sh` + `verify.py`, `d3.py` + `d3_proxy.py` (D3 measurement).

**Headless setup (`setup.mjs`, service `langfuse-setup`).** `LANGFUSE_INIT_*` on Web creates the
organisation, project, project keys and owner user at first start. `setup.mjs` then does what
the UI would: logs in as that user, sets the gateway config (ingestion project and mode) through
the tRPC route `aiGateway.updateConfig`, creates the OpenAI connection to the mock
(`http://mock-llm:9000/v1`), and creates a gateway key through `aiGateway.createApiKey`. Two
things are not clean API use, both in D15: Web v4.78.0 refuses a custom base URL in
`createConnection` outside development, preview and staging builds, so the connection row is
inserted with Prisma instead (same encryption and columns); and Web picks the key secret itself,
so the script overwrites the new key's stored hash with the hash of a fixed dummy secret
(`sk-lf-gwbench-gateway-key`) so k6 can use a constant key.

**ES256 key.** The key that signs the gateway's ingestion tokens is generated inside the Web
container at every start (a `node -e` in the entrypoint, in memory only), so no private key is
in the repository or in `compose.resolved.yml`.

**Overhead header.** The gateway sends no overhead header. Its response headers are
`langfuse-request-id`, `langfuse-trace-id`, `langfuse-observation-id`, `langfuse-organization-id`,
`langfuse-project-id`, `langfuse-provider` and `langfuse-provider-connection-id`; none is a duration.
It does export a `gateway.overhead.duration` metric over OTLP (`OTEL_EXPORTER_OTLP_ENDPOINT`), which
needs a collector and is not wired up. So `gateway_overhead_ms` stays empty for this target and
the comparison is the k6 latency against the S1 baseline (D17).

**`verify.sh` (`verify.py`).** Both profiles: all eight stack containers up, gateway limit and
image, allow-list on gateway and Web, `NEXT_PUBLIC_LANGFUSE_CLOUD_REGION` unset, the ingestion mode
in Postgres, the connection to the mock, a bad key refused with 401, a good request answered by the
mock through the gateway. Minimal: no `langfuse-trace-id` header, and 30 requests leave no
observation in the Langfuse project 25 s later. Prod: the header is there and all 30 generations
arrive in the project (the lag is printed), with token usage and no prompt or response (FULL: with).

**Gotchas**

- The gateway needs Web (and Web needs Redis, Postgres and ClickHouse) up before the first request;
  `up --wait` covers it, and a fresh stack takes 1 to 2 minutes because Web applies the Postgres and
  ClickHouse migrations at start.
- The Web and worker images bind to the container hostname, not `127.0.0.1`: healthchecks use
  `http://langfuse-web:3000` and `http://langfuse-worker:3030`.
- Keep `system.query_log` in ClickHouse: the worker reads it and logs an error every few minutes
  without it (`clickhouse-low-memory.xml` removes the other system log tables only).
- Overriding `entrypoint` in compose clears the image's command, so Web's `command` is part of the
  entrypoint script.
- Traces are best effort and not durable (async, batches of 512 spans / 4 MiB / 5 s): a request can
  succeed and leave no trace. `verify.py` waits for them and reports the lag.
