# Bifrost target

Bifrost HTTP gateway in front of the mock. Decisions: [D13 and D14](../../docs/decisions.md).
Contract for targets: [gateways/README.md](../README.md).

| | `minimal` | `prod` |
| --- | --- | --- |
| Containers | `gwbench-bifrost` | `gwbench-bifrost` (SQLite files inside it) |
| Config store | **off** (`config_store.enabled: false`) | on, SQLite `config.db` |
| Log store, logging plugin | **off** (`logs_store.enabled: false`, `client.enable_logging: false`) | on, SQLite `logs.db`, content logged |
| Telemetry (Prometheus `/metrics`) | off (plugin disabled) | on (default) |
| Auth | none enforced; governance plugin not loaded | virtual key `sk-bf-gwbench-virtual`, provider `openai`, model `mock-gpt`, budget 1,000,000 USD, `enforce_auth_on_inference` |
| Gateway limits | 2 CPU / 2 GB | 2 CPU / 2 GB (the whole stack) |

Image `maximhq/bifrost:v2.5.1` (arm64, digest in D13). Provider `openai` with
`base_url: http://mock-llm:9000` (**no `/v1`**: Bifrost appends `/v1/chat/completions`
itself). k6 base URL `http://bifrost:8080/v1`, route `POST /v1/chat/completions`, model
`openai/mock-gpt` (provider prefix required). Port `127.0.0.1:18080` is published for
`verify.sh` and poking around (`BIFROST_HOST_PORT`); the mock is at `mock-llm:9000`.

```bash
scripts/smoke.sh bifrost minimal       # up, plain + streaming request, verify.sh, down
scripts/smoke.sh bifrost prod --keep   # leave it running for poking around
make run TARGET=bifrost SCENARIO=s1 PROFILE=prod RATE=100 DURATION=20 WARMUP=5
BIFROST_GOGC=200 BIFROST_GOMEMLIMIT=1800MiB make run TARGET=bifrost SCENARIO=s1 PROFILE=minimal
```

Files: `target.env`, `docker-compose.minimal.yml`, `docker-compose.prod.yml`,
`config.minimal.json`, `config.prod.json` (mounted as `/app/data/config.json`),
`datasheet/` (local pricing and model-parameter files, see below), `verify.sh` +
`verify.py`.

**How minimal is proven to have no log store (`verify.py`).** The data directory is a
writable volume on purpose, so a store that switched itself on would leave a file. The check
lists `/app/data` before and after 30 requests: only `config.json`, no `*.db`, `*.db-wal`
or `*.db-shm`, empty `logs/`, same bytes. Also: `/api/logs` and `/api/logs/stats` answer
404 (route absent), `/metrics` 404, `/api/config` says `enable_logging: false`,
`/api/plugins` lists only `compat` and `model-catalog-resolver` as active. The AIGatewayBench
flaw (only `config_store` disabled) is reproduced in D13 and fails ten of these checks.
Prod asserts the opposite: both database files exist, `logs.db` grows during a burst, the
logs API lists the burst under the virtual key with Bifrost's own `overhead_latency`,
governance refuses a missing or wrong key and charges the budget, `/metrics` answers.

**Tuning knobs (defaults, in the configs; story #8 changes them).**

| Knob | Where | Value |
| --- | --- | --- |
| `concurrency_and_buffer_size.concurrency` | `providers.openai` | 1000 (workers per provider) |
| `concurrency_and_buffer_size.buffer_size` | `providers.openai` | 5000 (queue per provider, must be at least the concurrency) |
| `client.initial_pool_size` | global | 5000 (pre-allocated pool objects) |
| `client.drop_excess_requests` | global | false (a full queue blocks instead of returning 429) |
| `GOGC`, `GOMEMLIMIT` | env (`BIFROST_GOGC`, `BIFROST_GOMEMLIMIT`) | unset; docs suggest GOGC 200-400 and GOMEMLIMIT about 90% of 2 GB |
| `GOMAXPROCS` | automatic | 2, from the cgroup CPU limit |
| `network_config.max_conns_per_host` | `providers.openai` | default 5000, not set |

**Gateway-reported overhead.** Bifrost sends only `x-bifrost-upstream-latency-ms` (time
waiting on the provider). k6 records each request's `http_req_duration` minus that header
as `gateway_overhead_ms`, which `scripts/summarize.py` prints labelled as gateway-reported
(derived: it includes the k6 to Bifrost hop). Not comparable to LiteLLM's own header
(D13).

**Gotchas**

- Provider base URL with `/v1` gives HTTP 404 on every request (`/v1/v1/chat/completions`).
- `allow_private_network: true` is needed for the mock's Docker address, otherwise HTTP 502
  "connection to private IP ... is not allowed".
- Bifrost downloads its pricing datasheet from `getbifrost.ai` at start and dies if that fails;
  the local `datasheet/` files avoid it, and `mcp_library_sync_interval: 0` stops the second
  download in prod (D13).
- A virtual key with `allowed_models: ["*"]` returns 403 `model_blocked` for the mock's model
  (not in the model catalog); use the exact name.
- The prod management API (`/api/*`, `/metrics`) needs the header
  `x-bifrost-setup-token: gwbench-setup-token`. Minimal has no config store, so it is open.
- The governance init error in the minimal log ("governance config is nil") is expected: the
  plugin is not loaded without a config.
- Bifrost's JSON schema gives `initial_pool_size` 300; the code default is 5000.
- The 100 to 1000 RPS ladder does not saturate Bifrost on 2 CPUs: gateway CPU is 17% (minimal)
  and 30% (prod) of one core at 100 RPS and 58% / 75% at 1000, and p50 stays within about 2 ms
  of the baseline (scratch runs, numbers in D14).
