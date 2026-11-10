# ai-gateway-comparison

Reproducible performance benchmark for self-hosted AI gateways: **LiteLLM**, **Bifrost** and the **Langfuse AI Gateway**.

- **Phase 1:** gateway overhead against a deterministic mock LLM upstream (Docker Compose, k6).
- **Phase 2:** realism and correctness checks against real models via OpenRouter.

Work is tracked in the GitHub Project and the issues of this repo. Every run publishes its config, versions and raw results.

## Rules and decisions

- [docs/benchmark-contract.md](docs/benchmark-contract.md): the rules every run follows, and the scenario table S1-S10.
- [docs/decisions.md](docs/decisions.md): decisions D1-D5 behind the setup.

## Layout

| Path | Purpose |
| ---- | ------- |
| `docker-compose.base.yml` | Shared network (`gwbench`), mock upstream, k6, resource sampler, optional observability |
| `gateways/{litellm,bifrost,langfuse}/` | One compose overlay and config per gateway stack |
| `mock-llm/` | Deterministic mock upstream (hostname `mock-llm`, port `9000`), see [mock-llm/README.md](mock-llm/README.md) |
| `k6/` | Custom k6 image (`Dockerfile`, with xk6-sse), `smoke.js`, shared helpers in `lib/`, S1-S10 scenarios in `scenarios/` |
| `observability/` | Prometheus and Grafana config for the optional `obs` profile |
| `scripts/` | Runner and helper scripts, including the resource sampler |
| `results/` | Run output (raw data is git-ignored) |
| `report/` | Report generator and write-up |
| `docs/` | Contract and decisions |

## Usage

Needs Docker only. Run `make help` for targets. `make up`, `down`, `smoke` and `run` are placeholders until the runner and stacks land; the base stack targets are below. `make lint` runs the CI checks (compose config, shellcheck, ruff) in containers.

## Base stack

Services in `docker-compose.base.yml` (compose project `gwbench`, network `gwbench`). Containers are named `gwbench-<service>`; gateway overlays should follow that so scripts and the sampler can find them.

| Service | Container | What | Started by |
| ------- | --------- | ---- | ---------- |
| `mock-llm` | `gwbench-mock-llm` | Mock upstream, image `gwbench/mock-llm:0.1.0`, limit 2 CPU / 512 MB, healthcheck, published on `127.0.0.1:19000` | `make base-up` |
| `k6` | `gwbench-k6-run-*` | k6 v2.3.0 + xk6-sse v0.2.0, image `gwbench/k6:2.3.0-sse0.2.0` | one-off `docker compose run` |
| `sampler` | `gwbench-sampler` | CPU and memory per container, 1 s, CSV | `scripts/sampler.sh start` |
| `cadvisor`, `prometheus`, `grafana` | `gwbench-cadvisor`, `-prometheus`, `-grafana` | Optional dashboards, profile `obs` | `make obs-up` |

```bash
make k6-build        # build gwbench/k6:2.3.0-sse0.2.0 (about 2 min the first time)
make base-up         # mock-llm up and healthy
make base-smoke      # mock + k6 (plain and SSE request) + sampler CSV, end to end
make base-down       # remove everything this compose project started, with volumes
```

**Mock knobs.** Set `MOCK_*` variables in the shell or in `.env` before `make base-up` (see [mock-llm/README.md](mock-llm/README.md)). Defaults are the contract baseline: `MOCK_LATENCY_MS=60`, `MOCK_TTFT_MS=20`, `MOCK_ITL_MS=5`, `MOCK_OUTPUT_TOKENS=40`. Fault knobs are unset.

**Running k6.** `./k6` is mounted read-only at `/k6` (also the working directory) and `./results` at `/results`. The container is on the `gwbench` network, so targets are `http://mock-llm:9000/v1` or a gateway's container hostname. Pass settings with `-e`:

```bash
docker compose -f docker-compose.base.yml run --rm k6 run \
  -e TARGET_URL=http://mock-llm:9000/v1 -e DURATION=30s \
  --summary-export /results/myrun/summary.json /k6/smoke.js
```

Add `-o experimental-prometheus-rw --tag testid=<id>` to push metrics to the `obs` Prometheus (the server URL, trend stats and push interval are preset in the compose file).

**Streaming in k6.** `import sse from 'k6/x/sse'` and call `sse.open(url, {method, headers, body, tags}, setup)`; register `client.on('open' | 'event' | 'error', fn)` inside `setup` and call `client.close()` to end the stream (for OpenAI streams, on `event.data === '[DONE]'`). It returns `{status, headers, error}`. Time to first token is not measured for you: take `Date.now()` in the first `event` handler. See [k6/smoke.js](k6/smoke.js).

**Resource sampling.**

```bash
scripts/sampler.sh start results/<run>/resources.csv 'gwbench-mock-llm' 'gwbench-litellm*'
# ... run the test ...
scripts/sampler.sh stop
```

Arguments after the CSV path are container names or globs (resolved every 3 s, so restarted containers are picked up). The CSV is long format: `ts, container, cpu_percent, mem_bytes, mem_limit_bytes, pids, net_rx_bytes, net_tx_bytes` (100 % CPU = one core). Why this and not cAdvisor: [docs/decisions.md](docs/decisions.md) D6.

**Observability (optional).** `make obs-up` starts cAdvisor, Prometheus (remote-write receiver on, `127.0.0.1:19090`) and Grafana (`http://127.0.0.1:13000`, dashboard "gwbench overview", anonymous viewer, admin password `admin`). About 200 MB in total. It shares the VM with the system under test, so leave it off for headline runs. `make obs-down` stops it.

## Licence

MIT, see [LICENSE](LICENSE).
