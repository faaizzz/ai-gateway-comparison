.DEFAULT_GOAL := help
.PHONY: help up down smoke run lint base-up base-down base-smoke k6-build obs-up obs-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-11s %s\n", $$1, $$2}'

# Runner variables: TARGET (direct, litellm, bifrost, langfuse), SCENARIO (s1, s1-ramp, ...),
# and optionally PROFILE, REPEAT, DURATION (s per step), RATE (rps, or a comma list), WARMUP (s),
# RAW=1 (per-request JSON), DATE (results folder name under results/, default today; use a scratch
# name such as DATE=scratch-x for short or trial runs, results/scratch*/ is gitignored). Example: make run TARGET=direct SCENARIO=s1 REPEAT=3
RUN_FLAGS = $(if $(PROFILE),--profile $(PROFILE)) $(if $(REPEAT),--repeat $(REPEAT)) \
  $(if $(DURATION),--duration $(DURATION)) $(if $(RATE),--rate $(RATE)) $(if $(WARMUP),--warmup $(WARMUP)) \
  $(if $(RAW),--raw) $(if $(KEEP),--keep) $(if $(DATE),--date $(DATE))

up: ## Start the mock and a target's stack: make up TARGET=litellm [PROFILE=prod]
	./scripts/stack.sh up $(TARGET) $(if $(PROFILE),--profile $(PROFILE))

down: ## Remove the mock and any gateway stack, with volumes
	./scripts/stack.sh down $(TARGET) $(if $(PROFILE),--profile $(PROFILE))

smoke: ## Plain + streaming request and mode check against an up target: make smoke TARGET=litellm [PROFILE=prod]
	./scripts/smoke.sh $(TARGET) $(PROFILE) --no-up

run: ## Run a scenario end to end: make run TARGET=direct SCENARIO=s1 [PROFILE REPEAT DURATION RATE WARMUP RAW DATE]
	./scripts/run.sh $(TARGET) $(SCENARIO) $(RUN_FLAGS)

base-up: ## Start the mock upstream and wait until it is healthy
	docker compose -f docker-compose.base.yml up -d --wait mock-llm

base-down: ## Stop the base stack (and obs, k6, sampler) and remove its containers and volumes
	-./scripts/sampler.sh stop 2>/dev/null
	docker compose -f docker-compose.base.yml --profile '*' down -v --remove-orphans

base-smoke: ## Check the base stack end to end: mock, k6 with SSE, sampler CSV
	./scripts/base-smoke.sh

k6-build: ## Build the custom k6 image with xk6-sse (gwbench/k6:2.3.0-sse0.2.0)
	docker compose -f docker-compose.base.yml build k6

obs-up: ## Start optional Prometheus + Grafana + cAdvisor (Grafana on http://localhost:13000)
	docker compose -f docker-compose.base.yml --profile obs up -d --wait

obs-down: ## Stop the optional observability containers
	docker compose -f docker-compose.base.yml --profile obs rm -sf cadvisor prometheus grafana

lint: ## Run the same checks as CI (compose config, shellcheck, ruff), in containers
	./scripts/ci-checks.sh
