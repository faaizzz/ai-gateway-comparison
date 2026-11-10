.DEFAULT_GOAL := help
.PHONY: help up down smoke run lint base-up base-down base-smoke k6-build obs-up obs-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-11s %s\n", $$1, $$2}'

up: ## Start the base stack and one gateway (placeholder, story #4)
	@echo "not implemented yet (story #4)"

down: ## Stop everything and remove volumes (placeholder, story #4)
	@echo "not implemented yet (story #4)"

smoke: ## Quick check that a gateway answers through to the mock (placeholder, stories #5-#7)
	@echo "not implemented yet (stories #5-#7)"

run: ## Run a scenario against a gateway (placeholder, story #4)
	@echo "not implemented yet (story #4)"

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
