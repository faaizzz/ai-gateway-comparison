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
| `docker-compose.base.yml` | Shared network (`gwbench`), mock upstream, k6, sampling (filled in by later stories) |
| `gateways/{litellm,bifrost,langfuse}/` | One compose overlay and config per gateway stack |
| `mock-llm/` | Deterministic mock upstream (hostname `mock-llm`, port `9000`), see [mock-llm/README.md](mock-llm/README.md) |
| `k6/lib/`, `k6/scenarios/` | Shared k6 helpers and the S1-S10 scenarios |
| `scripts/` | Runner and helper scripts |
| `results/` | Run output (raw data is git-ignored) |
| `report/` | Report generator and write-up |
| `docs/` | Contract and decisions |

## Usage

Needs Docker only. Run `make help` for targets. `make up`, `down`, `smoke` and `run` are placeholders until the runner and stacks land. `make lint` runs the CI checks (compose config, shellcheck, ruff) in containers.

## Licence

MIT, see [LICENSE](LICENSE).
