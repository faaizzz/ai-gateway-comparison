# Gateway targets

A target is a directory `gateways/<name>/` with a `target.env`. The runner
(`scripts/run.sh`) and the stack helper (`scripts/stack.sh`) read nothing else
about a gateway, so adding LiteLLM, Bifrost or Langfuse means adding files here
and does not mean editing the scripts. `direct` (k6 straight to the mock, the S1
baseline) is the reference example.

```
gateways/<name>/
  target.env                    required: the contract below
  docker-compose.<profile>.yml  compose overlay(s) on docker-compose.base.yml (name them as you like, list them in target.env)
  *.yaml / *.json / *.toml      the gateway's own config files
  verify.sh                     optional: checks the profile's mode is really active (contract rule 6)
```

## target.env

A bash file, `source`d with the repo root as the working directory and `PROFILE`
set to the chosen profile (`minimal` or `prod`), so it can build file names from
it. Plain `KEY=value` lines; keep it free of side effects.

| Variable | Required | Meaning |
| -------- | -------- | ------- |
| `TARGET_DESCRIPTION` | no | One line for humans |
| `TARGET_PROFILES` | no | Space-separated profiles this target supports. Default `minimal prod`. The first one is the default. `direct` uses `none` |
| `TARGET_COMPOSE_FILES` | for gateways | Space-separated compose overlays, relative to the repo root, merged after `docker-compose.base.yml`. Usually `gateways/<name>/docker-compose.$PROFILE.yml`. Empty for `direct` |
| `TARGET_BASE_URL` | yes | OpenAI-style base URL **as seen from the k6 container on the `gwbench` network**, with the `/v1` prefix: k6 posts to `$TARGET_BASE_URL/chat/completions` |
| `TARGET_MODEL` | yes | Model name k6 sends, as the gateway knows it |
| `TARGET_API_KEY` | yes | Bearer token k6 sends (the benchmark's own fake key, never a real secret) |
| `TARGET_HEALTH_URL` | no | A URL k6 `GET`s in the smoke test (must answer 200), also from inside the network |
| `TARGET_CONFIG_FILES` | no | Space-separated files (relative to the repo root) copied into every run's `config/` as the config snapshot |
| `TARGET_CONTAINERS` | no | Container-name globs for the resource sampler. Default `gwbench-*` (everything, including the mock and k6) |
| `TARGET_SMOKE_TIMEOUT_S` | no | How long the smoke request may keep failing while the gateway warms up. Default 120 |

Compose variables `GWBENCH_TARGET` and `GWBENCH_PROFILE` are exported for
interpolation in overlays. Anything else `target.env` `export`s is visible to
compose too (use that for generated settings). Mock knobs (`MOCK_*`) come from
the caller's environment, see `docker-compose.base.yml`.

## Compose overlay rules

- It is an overlay on the base: it adds services, and does not redefine `mock-llm`
  or the network. CI validates it merged with `docker-compose.base.yml`, without
  a `.env`, so every `${VAR}` needs a default.
- Container names are `gwbench-<service>` (the sampler picks up `gwbench-*`).
- The gateway container gets `cpus: 2` and `mem_limit: 2g` (with `memswap_limit`
  equal, so there is no swap). Every other container of the stack (databases,
  caches, log stores) gets its own explicit limit, because contract rule 1 reports
  the whole stack.
- Every service has a healthcheck; `up --wait` blocks until they are green. Pin
  all images by version tag.
- The gateway reaches the mock at `http://mock-llm:9000` (or `.../v1`, whichever
  the gateway wants), see `mock-llm/README.md`.
- Do not publish ports unless you need them for debugging, and then only on
  `127.0.0.1`.
- Keep gateway state inside the stack's compose volumes: the runner tears down
  with `down -v`, so every run starts from a clean database.

## verify.sh (optional but expected for gateways)

Contract rule 6: before measuring, prove the profile is what it claims and that
traffic goes through the gateway to the mock. The runner calls
`bash gateways/<name>/verify.sh` on the host after the smoke test, with the repo
root as working directory and `TARGET` and `PROFILE` in the environment. Exit
non-zero to abort the run. Typical checks: send one request with `docker compose
... exec` or `curl http://127.0.0.1:<published port>`, look at response headers,
look at the gateway's own counters or database, and confirm the mock saw the
call. The smoke request itself (k6, `k6/smoke_target.js`) already fails unless
the answer is the mock's text, so a request that was served without reaching the
mock cannot pass.

## What the runner does with a target

1. `docker compose -f base [-f overlay...] up -d --wait --remove-orphans`
   (mock and the target stack; any other gateway stack is removed, so one stack at
   a time).
2. Smoke request with retries (`k6/smoke_target.js`), then `verify.sh` if present.
3. Starts the sampler, runs the scenario's k6 script with `TARGET_URL`, `MODEL`,
   `API_KEY`, `BENCH_TARGET`, `BENCH_PROFILE`, `BENCH_SCENARIO` (see
   `k6/lib/env.js`).
4. Writes `metadata.json`, `compose.resolved.yml` and `config/` (the files in
   `TARGET_CONFIG_FILES`), then `down -v`.

## Adding a gateway: checklist

1. `gateways/<name>/target.env` per the table above.
2. Overlay compose file(s) following the rules above.
3. A `verify.sh` for each profile's claims.
4. `make lint` (validates the overlay) and `make smoke TARGET=<name>`.
5. `make run TARGET=<name> SCENARIO=s1 RATE=100 DURATION=20` as a quick end-to-end
   check before real runs.

## Adding a scenario

Scenarios live in `k6/scenarios/`; the table `k6/scenarios/scenarios.txt` maps an
id to a script. Add a line `s2  s2_equal_rate.js  description` and the script.
Use `k6/lib/` for the pieces every scenario shares: `env.js` (target, model, key,
tags), `payload.js`, `chat.js` (one checked request), `arrival.js` (open-loop
steps with the warm-up excluded, per-step sub-metrics in `summary.json`) and
`ladder.js` (the Phase 1 rate ladder). `k6/scenarios/s1_baseline.js` is a
12-line example. Nothing else changes: `make run TARGET=<t> SCENARIO=s2` works.
