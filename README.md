# ai-gateway-comparison

Reproducible performance benchmark for self-hosted AI gateways: **LiteLLM**, **Bifrost** and the **Langfuse AI Gateway**.

- **Phase 1:** gateway overhead against a deterministic mock LLM upstream (Docker Compose, k6).
- **Phase 2:** realism and correctness checks against real models via OpenRouter.

Work is tracked in the GitHub Project and the issues of this repo. Every run publishes its config, versions and raw results.

## Rules and decisions

- [docs/benchmark-contract.md](docs/benchmark-contract.md): the rules every run follows, and the scenario table S1-S10.
- [docs/decisions.md](docs/decisions.md): decisions behind the setup (mock reuse, Langfuse allow-list, sampling, k6 build, rate ladder, durations, LiteLLM image and profiles, Bifrost image, route and profiles, the Langfuse go/no-go and its Web calls).

## Layout

| Path | Purpose |
| ---- | ------- |
| `docker-compose.base.yml` | Shared network (`gwbench`), mock upstream, k6, resource sampler, optional observability |
| `gateways/<target>/` | One directory per target: `target.env`, compose overlay, config, optional `verify.sh`. `direct` is the baseline, `litellm` is in [gateways/litellm/](gateways/litellm/README.md), `bifrost` in [gateways/bifrost/](gateways/bifrost/README.md), `langfuse` (self-hosted Langfuse plus its AI gateway) in [gateways/langfuse/](gateways/langfuse/README.md). Contract in [gateways/README.md](gateways/README.md) |
| `mock-llm/` | Deterministic mock upstream (hostname `mock-llm`, port `9000`), see [mock-llm/README.md](mock-llm/README.md) |
| `k6/` | Custom k6 image (`Dockerfile`, with xk6-sse), `smoke.js`, `smoke_target.js`, shared helpers in `lib/`, scenarios and their table in `scenarios/` |
| `observability/` | Prometheus and Grafana config for the optional `obs` profile |
| `scripts/` | Runner (`run.sh`, `stack.sh`, `lib.sh`), `metadata.py`, `summarize.py`, `sustained.py` (D15/D24 verdict per step), `tune.sh` (tuning candidates, D25), resource sampler |
| `results/` | Run output (raw data is git-ignored) |
| `report/` | Report generator and write-up |
| `docs/` | Contract and decisions |

## Usage

Needs Docker only (plus Python 3 for the metadata step). Run `make help` for targets. `make lint` runs the CI checks (compose config, shellcheck, ruff) in containers.

```bash
make run TARGET=direct SCENARIO=s1                  # S1 baseline at the rate ladder, about 5 min
make run TARGET=direct SCENARIO=s1 REPEAT=3         # three independent runs, each on a fresh stack
make run TARGET=direct SCENARIO=s1-ramp             # mock capacity check, about 3 min
make run TARGET=direct SCENARIO=s1 RATE=100 DURATION=20 WARMUP=5 DATE=scratch-x   # quick check, written to results/scratch-x/ (gitignored)
make up TARGET=<gateway> PROFILE=prod; make smoke TARGET=<gateway>; make down   # by hand
scripts/smoke.sh litellm prod                       # up, plain + streaming request, mode check, down
scripts/smoke.sh bifrost minimal                    # same for Bifrost (log store verified off)
scripts/smoke.sh langfuse prod                      # same for Langfuse (8 containers, about 2 min to start)
```

`make run` calls `scripts/run.sh <target> <scenario> [--profile minimal|prod] [--repeat N] [--duration S] [--rate R[,R...]] [--warmup S] [--raw] [--keep] [--date D]` (`make run ... DATE=D`; results go to `results/<D>/`, today's date by default, and `results/scratch*/` is gitignored, so short and trial runs use a `scratch-*` name). Per run it starts the mock and the target stack, waits for health, sends a smoke request and runs the target's `verify.sh`, starts the resource sampler, runs k6 (warm-up excluded), writes the metadata and tears the stack down. Defaults: 15 s warm-up and 60 s per step (S1 ramp: 10 s and 15 s), so S1 at the four-step ladder takes about 5 minutes. The decisions behind the durations and rates are in [docs/decisions.md](docs/decisions.md) (D8).

Results land in `results/<YYYY-MM-DD>/<target>/<profile>/<scenario>/run-<n>/`:

| File | Content |
| ---- | ------- |
| `summary.json` | k6 summary export. Read the per-step sub-metrics, e.g. `http_req_duration{step:r500}`; whole-run totals include warm-up |
| `resources.csv` | CPU and memory of every container, 1 s |
| `metadata.json` | git SHA, image tags and digests, mock knobs, container limits, host and Docker resources, k6 step plan and timing |
| `k6.log`, `compose.resolved.yml`, `config/` | k6 console output, resolved compose config, snapshot of the gateway's config files |
| `raw/` | Per-request k6 output, only with `--raw`; git-ignored |

`scripts/summarize.py <run-dir>` prints a per-step table (achieved rate, p50/p95/p99, errors, CPU). `direct` is the S1 baseline target (no gateway, profile `none`).

Adding a gateway or a scenario: [gateways/README.md](gateways/README.md).

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
