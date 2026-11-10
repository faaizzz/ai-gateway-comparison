# ai-gateway-comparison

Reproducible performance benchmark for self-hosted AI gateways: **LiteLLM**, **Bifrost** and the **Langfuse AI Gateway**.

- **Phase 1:** gateway overhead against a deterministic mock LLM upstream (Docker Compose, k6).
- **Phase 2:** realism and correctness checks against real models via OpenRouter.

Work is tracked in the GitHub Project and the issues of this repo. Every run publishes its config, versions and raw results.
