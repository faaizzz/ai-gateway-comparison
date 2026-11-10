.DEFAULT_GOAL := help
.PHONY: help up down smoke run lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

up: ## Start the base stack and one gateway (placeholder, story #4)
	@echo "not implemented yet (story #4)"

down: ## Stop everything and remove volumes (placeholder, story #4)
	@echo "not implemented yet (story #4)"

smoke: ## Quick check that a gateway answers through to the mock (placeholder, stories #5-#7)
	@echo "not implemented yet (stories #5-#7)"

run: ## Run a scenario against a gateway (placeholder, story #4)
	@echo "not implemented yet (story #4)"

lint: ## Run the same checks as CI (compose config, shellcheck, ruff), in containers
	./scripts/ci-checks.sh
