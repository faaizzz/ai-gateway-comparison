# mock-llm

Deterministic OpenAI- and Anthropic-compatible mock upstream for the gateway
benchmark (decision D1). Vendored from
[BerriAI/ai-gateway-bench](https://github.com/BerriAI/ai-gateway-bench) (MIT),
see [NOTICE](NOTICE). Hostname `mock-llm`, port `9000` on the `gwbench` network.

## Build and run

```bash
docker build -t gwbench/mock-llm:0.1.0 mock-llm
docker run --rm -p 9000:9000 gwbench/mock-llm:0.1.0
```

The image runs as a non-root user, sets `MOCK_HOST=0.0.0.0` and has a
`HEALTHCHECK` (the binary probes its own `/health`; there is no curl in the
image). It builds natively on arm64.

## Routes

Any model name is accepted, and any `Authorization` or `x-api-key` header (they
are ignored). The request body does not need a JSON content type. Responses
always carry the model `mock-upstream` and a fixed text of `MOCK_OUTPUT_TOKENS`
words, so response size is constant.

| Method and path | Notes |
| --------------- | ----- |
| `GET /health` | `{"status":"ok"}`, never faulted |
| `POST /v1/chat/completions` | OpenAI Chat Completions. `/chat/completions` is served too |
| `POST /v1/messages` | Anthropic Messages. `/messages` is served too |
| `GET /v1/models`, `GET /models` | One model, `mock-upstream` |

Base URL for OpenAI-style gateways: `http://mock-llm:9000/v1` (the gateway adds
`/chat/completions`) or `http://mock-llm:9000` (the gateway adds
`/v1/chat/completions`). For Anthropic use `http://mock-llm:9000`.

## Knobs (environment)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `MOCK_TTFT_MS` | 20 | Streaming: delay before the first chunk. Non-streaming: the whole response delay, unless `MOCK_LATENCY_MS` is set |
| `MOCK_ITL_MS` | 5 | Streaming: delay before each content chunk |
| `MOCK_OUTPUT_TOKENS` | 40 | Number of `token ` words in the answer (also reported as completion tokens) |
| `MOCK_LATENCY_MS` | unset | Total delay of non-streaming responses. Overrides `MOCK_TTFT_MS` there; does not affect streaming |
| `MOCK_FAIL_STATUS` | unset | Error status (400-599) for injected failures. Alone, it fails every request |
| `MOCK_FAIL_RATE` | 0, or 1 if `MOCK_FAIL_STATUS` is set | Fraction (0-1) of requests answered with `MOCK_FAIL_STATUS` (500 if unset) |
| `MOCK_429_RATE` | 0 | Fraction (0-1) of requests answered 429 with a `Retry-After` header |
| `MOCK_RETRY_AFTER_S` | 1 | Value of `Retry-After` in seconds (also used if `MOCK_FAIL_STATUS=429`) |
| `MOCK_WORKER_THREADS` | 8 | Tokio worker threads |
| `MOCK_HOST` | `0.0.0.0` in the image (`127.0.0.1` for a bare binary) | Listen address |
| `MOCK_PORT` | 9000 | Listen port |

### Latency behaviour

- **Non-streaming:** one sleep of `MOCK_LATENCY_MS` (or `MOCK_TTFT_MS` if that is
  unset), then the full body. `MOCK_ITL_MS` and `MOCK_OUTPUT_TOKENS` do not
  change the delay. With defaults a non-streaming call takes about 20 ms.
- **Streaming:** `MOCK_TTFT_MS`, then one chunk every `MOCK_ITL_MS`. The default
  is about 20 + 40 x 5 = 220 ms of ideal time. Timer rounding adds to this;
  measured in Docker Desktop on an M1 the gap between chunks was about 7 ms for
  `MOCK_ITL_MS=5` and a full stream took about 325 ms. Compare gateways against
  the direct-to-mock baseline, not against the ideal figures.
- **Streaming TTFT:** response headers are sent immediately, before the first
  chunk. A client timer that stops at the response headers (k6
  `http_req_waiting`) does not see `MOCK_TTFT_MS`. Measure time to the first
  `data:` chunk for time to first token.

### Fault injection

Each request that has any fault rate set draws one number from a deterministic
splitmix64 sequence (it restarts on every container start): the first
`MOCK_FAIL_RATE` of the range fails, the next `MOCK_429_RATE` answers 429, the
rest succeed. The two rates therefore never overlap and add up (clamped at 1).
Faults apply before the body is read, on both routes and for streaming
requests too (the error is returned instead of a stream). Error bodies are JSON
`{"error":{"message","type","status"}}`.

Measured over 20000 requests: `MOCK_FAIL_RATE=0.2` gave 19.9 % failures,
`MOCK_429_RATE=0.3` gave 30.3 % 429s.

### Usage

Non-streaming OpenAI and Anthropic responses include token usage
(`prompt_tokens: 1`, `completion_tokens: MOCK_OUTPUT_TOKENS`). OpenAI streams
add a final usage chunk (empty `choices`) before `[DONE]` when the request sets
`stream_options.include_usage`, and then carry `"usage": null` on every other
chunk, as OpenAI does. Anthropic streams always carry usage in `message_start`
and `message_delta`.

## Examples

OpenAI, non-streaming and streaming with usage:

```bash
curl -s localhost:9000/v1/chat/completions -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer anything' \
  -d '{"model":"any-model","messages":[{"role":"user","content":"hi"}]}'

curl -sN localhost:9000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"any-model","stream":true,"stream_options":{"include_usage":true},
       "messages":[{"role":"user","content":"hi"}]}'
```

Anthropic, non-streaming and streaming:

```bash
curl -s localhost:9000/v1/messages -H 'Content-Type: application/json' \
  -H 'x-api-key: anything' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"any-model","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'

curl -sN localhost:9000/v1/messages -H 'Content-Type: application/json' \
  -d '{"model":"any-model","max_tokens":64,"stream":true,
       "messages":[{"role":"user","content":"hi"}]}'
```

Anthropic requests with `tools` (or `"_gwbench_tool": true`) get a fixed
`tool_use` answer, as upstream.

Faults, and a quick rate check:

```bash
docker run --rm -d --name mock -p 9000:9000 \
  -e MOCK_FAIL_RATE=0.2 -e MOCK_429_RATE=0.1 gwbench/mock-llm:0.1.0
for i in $(seq 1000); do
  curl -s -o /dev/null -w '%{http_code}\n' localhost:9000/v1/chat/completions \
    -H 'Content-Type: application/json' -d '{}'
done | sort | uniq -c
docker rm -f mock
```
