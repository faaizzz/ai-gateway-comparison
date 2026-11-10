#![forbid(unsafe_code)]

use std::convert::Infallible;
use std::env;
use std::io::{Read, Write};
use std::net::{IpAddr, TcpStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Json, State};
use axum::http::header::{CONTENT_TYPE, RETRY_AFTER};
use axum::http::{HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bytes::Bytes;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

const WORD: &str = "token ";
const TOOL_FRAGMENTS: [&str; 5] = [
    r#"{"path":"#,
    r#" "src/main"#,
    r#".rs","content":"#,
    r#" "fn main"#,
    r#"() {}"}"#,
];

const GOLDEN: u64 = 0x9E37_79B9_7F4A_7C15;

#[derive(Clone)]
struct MockConfig {
    ttft: Duration,
    itl: Duration,
    /// Total latency of non-streaming responses. Falls back to `ttft`.
    latency: Option<Duration>,
    output_tokens: usize,
    /// Status returned for injected failures (MOCK_FAIL_STATUS, default 500).
    fail_status: u16,
    /// Fraction of requests answered with `fail_status`.
    fail_rate: f64,
    /// Fraction of requests answered 429 with Retry-After.
    rate_429: f64,
    retry_after_secs: u64,
    /// Request counter feeding the fault dice (splitmix64, deterministic).
    counter: Arc<AtomicU64>,
}

fn env_parse<T: std::str::FromStr>(name: &str) -> Option<T> {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<T>().ok())
}

fn env_rate(name: &str) -> Option<f64> {
    env_parse::<f64>(name)
        .filter(|rate| rate.is_finite())
        .map(|rate| rate.clamp(0.0, 1.0))
}

impl MockConfig {
    fn from_env() -> Self {
        let ttft_ms = env_parse::<u64>("MOCK_TTFT_MS").unwrap_or(20);
        let itl_ms = env_parse::<u64>("MOCK_ITL_MS").unwrap_or(5);
        let output_tokens = env_parse::<usize>("MOCK_OUTPUT_TOKENS").unwrap_or(40);
        let fail_status =
            env_parse::<u16>("MOCK_FAIL_STATUS").filter(|status| (400..=599).contains(status));
        // Upstream behaviour: MOCK_FAIL_STATUS alone fails every request.
        // MOCK_FAIL_RATE narrows that to a fraction (status defaults to 500).
        let fail_rate =
            env_rate("MOCK_FAIL_RATE").unwrap_or(if fail_status.is_some() { 1.0 } else { 0.0 });
        Self {
            ttft: Duration::from_millis(ttft_ms),
            itl: Duration::from_millis(itl_ms),
            latency: env_parse::<u64>("MOCK_LATENCY_MS").map(Duration::from_millis),
            output_tokens,
            fail_status: fail_status.unwrap_or(500),
            fail_rate,
            rate_429: env_rate("MOCK_429_RATE").unwrap_or(0.0),
            retry_after_secs: env_parse::<u64>("MOCK_RETRY_AFTER_S").unwrap_or(1),
            counter: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Uniform value in [0, 1), one per request, from a splitmix64 sequence.
    fn next_unit(&self) -> f64 {
        let state = self
            .counter
            .fetch_add(GOLDEN, Ordering::Relaxed)
            .wrapping_add(GOLDEN);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^= z >> 31;
        (z >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Latency to apply before a non-streaming response.
    fn non_stream_delay(&self) -> Duration {
        self.latency.unwrap_or(self.ttft)
    }
}

fn main() {
    if env::args().any(|arg| arg == "--healthcheck") {
        std::process::exit(if healthcheck() { 0 } else { 1 });
    }
    let config = MockConfig::from_env();
    let workers = env_parse::<usize>("MOCK_WORKER_THREADS")
        .filter(|threads| *threads > 0)
        .unwrap_or(8);
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers)
        .enable_all()
        .build()
        .expect("tokio runtime builds")
        .block_on(serve(config));
}

/// `mock-upstream --healthcheck`: GET /health on localhost, for the image
/// HEALTHCHECK (the runtime image has no curl).
fn healthcheck() -> bool {
    let port = env_parse::<u16>("MOCK_PORT").unwrap_or(9000);
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    let mut response = String::new();
    stream.write_all(request).is_ok()
        && stream.read_to_string(&mut response).is_ok()
        && response.starts_with("HTTP/1.1 200")
}

async fn serve(config: MockConfig) {
    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(models))
        .route("/models", get(models))
        .route("/v1/messages", post(messages))
        .route("/messages", post(messages))
        .route("/v1/chat/completions", post(chat_completions))
        .route("/chat/completions", post(chat_completions))
        .with_state(config);
    let address: IpAddr = env::var("MOCK_HOST")
        .unwrap_or_else(|_| "127.0.0.1".to_string())
        .parse()
        .expect("MOCK_HOST must be a valid IP address");
    let port = env_parse::<u16>("MOCK_PORT").unwrap_or(9000);
    let listener = tokio::net::TcpListener::bind((address, port))
        .await
        .expect("mock upstream binds");
    axum::serve(listener, app)
        .await
        .expect("mock upstream serves");
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

async fn models() -> Json<Value> {
    Json(json!({
        "object": "list",
        "data": [{
            "id": "mock-upstream",
            "object": "model",
            "created": 0,
            "owned_by": "gwbench"
        }]
    }))
}

fn error_response(status: u16, kind: &str, message: &str, retry_after_secs: u64) -> Response {
    let mut response = (
        StatusCode::from_u16(status).expect("failure status is in HTTP error range"),
        Json(json!({
            "error": {
                "message": message,
                "type": kind,
                "status": status
            }
        })),
    )
        .into_response();
    if status == 429 {
        response.headers_mut().insert(
            RETRY_AFTER,
            HeaderValue::from_str(&retry_after_secs.to_string()).expect("numeric header value"),
        );
    }
    response
}

/// Injected fault for this request, if any. One draw per request: the first
/// `fail_rate` of the unit interval fails, the next `rate_429` answers 429, so
/// the two fractions never overlap (they are clamped to sum to at most 1).
fn failure_response(config: &MockConfig) -> Option<Response> {
    if config.fail_rate <= 0.0 && config.rate_429 <= 0.0 {
        return None;
    }
    let draw = config.next_unit();
    if draw < config.fail_rate {
        return Some(error_response(
            config.fail_status,
            "mock_failure",
            "deterministic mock failure",
            config.retry_after_secs,
        ));
    }
    if draw < config.fail_rate + config.rate_429 {
        return Some(error_response(
            429,
            "rate_limit_error",
            "deterministic mock rate limit",
            config.retry_after_secs,
        ));
    }
    None
}

fn parse_body(raw: &Bytes) -> Value {
    serde_json::from_slice(raw).unwrap_or(Value::Null)
}

fn wants_tool_call(body: &Value) -> bool {
    body.get("tools").is_some_and(value_is_truthy)
        || body.get("_gwbench_tool").and_then(Value::as_bool) == Some(true)
}

fn value_is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn anthropic_body(config: &MockConfig, tool_call: bool) -> Value {
    let content = if tool_call {
        json!([{
            "type": "tool_use",
            "id": "toolu_gwbench",
            "name": "edit_file",
            "input": {
                "path": "src/main.rs",
                "content": "fn main() {}"
            }
        }])
    } else {
        json!([{"type": "text", "text": WORD.repeat(config.output_tokens)}])
    };
    json!({
        "id": "msg_gwbench",
        "type": "message",
        "role": "assistant",
        "model": "mock-upstream",
        "content": content,
        "stop_reason": if tool_call { "tool_use" } else { "end_turn" },
        "stop_sequence": null,
        "usage": {
            "input_tokens": 1,
            "output_tokens": config.output_tokens
        }
    })
}

fn sse_event(kind: &str, payload: Value) -> Bytes {
    Bytes::from(format!(
        "event: {kind}\ndata: {}\n\n",
        serde_json::to_string(&payload).expect("SSE payload serializes")
    ))
}

fn anthropic_stream(
    config: MockConfig,
    tool_call: bool,
) -> ReceiverStream<Result<Bytes, Infallible>> {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        let message_start = json!({
            "type": "message_start",
            "message": {
                "id": "msg_gwbench",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "mock-upstream",
                "stop_reason": null,
                "stop_sequence": null,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": config.output_tokens
                }
            }
        });
        if sender
            .send(Ok(sse_event("message_start", message_start)))
            .await
            .is_err()
        {
            return;
        }
        tokio::time::sleep(config.ttft).await;
        if tool_call {
            let start = json!({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_gwbench",
                    "name": "edit_file",
                    "input": {}
                }
            });
            if sender
                .send(Ok(sse_event("content_block_start", start)))
                .await
                .is_err()
            {
                return;
            }
            for fragment in TOOL_FRAGMENTS {
                tokio::time::sleep(config.itl).await;
                let delta = json!({
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": fragment
                    }
                });
                if sender
                    .send(Ok(sse_event("content_block_delta", delta)))
                    .await
                    .is_err()
                {
                    return;
                }
            }
            let stop = json!({"type": "content_block_stop", "index": 0});
            if sender
                .send(Ok(sse_event("content_block_stop", stop)))
                .await
                .is_err()
            {
                return;
            }
        } else {
            let start = json!({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "text",
                    "text": ""
                }
            });
            if sender
                .send(Ok(sse_event("content_block_start", start)))
                .await
                .is_err()
            {
                return;
            }
            for _ in 0..config.output_tokens {
                tokio::time::sleep(config.itl).await;
                let delta = json!({
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": WORD
                    }
                });
                if sender
                    .send(Ok(sse_event("content_block_delta", delta)))
                    .await
                    .is_err()
                {
                    return;
                }
            }
            let stop = json!({"type": "content_block_stop", "index": 0});
            if sender
                .send(Ok(sse_event("content_block_stop", stop)))
                .await
                .is_err()
            {
                return;
            }
        }
        let message_delta = json!({
            "type": "message_delta",
            "delta": {
                "stop_reason": if tool_call { "tool_use" } else { "end_turn" }
            },
            "usage": {"output_tokens": config.output_tokens}
        });
        if sender
            .send(Ok(sse_event("message_delta", message_delta)))
            .await
            .is_err()
        {
            return;
        }
        let _ = sender
            .send(Ok(sse_event(
                "message_stop",
                json!({"type": "message_stop"}),
            )))
            .await;
    });
    ReceiverStream::new(receiver)
}

async fn messages(State(config): State<MockConfig>, raw: Bytes) -> Response {
    if let Some(response) = failure_response(&config) {
        return response;
    }
    let body = parse_body(&raw);
    let tool_call = wants_tool_call(&body);
    if body.get("stream").and_then(Value::as_bool) == Some(true) {
        return Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "text/event-stream")
            .body(Body::from_stream(anthropic_stream(config, tool_call)))
            .expect("stream response builds");
    }
    tokio::time::sleep(config.non_stream_delay()).await;
    Json(anthropic_body(&config, tool_call)).into_response()
}

fn openai_body(config: &MockConfig) -> Value {
    json!({
        "id": "chatcmpl-gwbench",
        "object": "chat.completion",
        "created": 0,
        "model": "mock-upstream",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": WORD.repeat(config.output_tokens)
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": config.output_tokens,
            "total_tokens": config.output_tokens + 1
        }
    })
}

fn include_usage(body: &Value) -> bool {
    body.get("stream_options")
        .and_then(|options| options.get("include_usage"))
        .and_then(Value::as_bool)
        == Some(true)
}

fn openai_chunk(payload: &Value) -> Bytes {
    Bytes::from(format!(
        "data: {}\n\n",
        serde_json::to_string(payload).expect("SSE payload serializes")
    ))
}

/// With `stream_options.include_usage` OpenAI puts `"usage": null` on every
/// chunk and ends with one extra chunk that has empty `choices` and the usage.
fn openai_stream(
    config: MockConfig,
    include_usage: bool,
) -> ReceiverStream<Result<Bytes, Infallible>> {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        let chunk = |choices: Value| {
            let mut payload = json!({
                "id": "chatcmpl-gwbench",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock-upstream",
                "choices": choices
            });
            if include_usage {
                payload["usage"] = Value::Null;
            }
            openai_chunk(&payload)
        };
        tokio::time::sleep(config.ttft).await;
        let first = chunk(json!([{
            "index": 0,
            "delta": {"role": "assistant", "content": ""},
            "finish_reason": null
        }]));
        if sender.send(Ok(first)).await.is_err() {
            return;
        }
        for _ in 0..config.output_tokens {
            tokio::time::sleep(config.itl).await;
            let delta = chunk(json!([{
                "index": 0,
                "delta": {"content": WORD},
                "finish_reason": null
            }]));
            if sender.send(Ok(delta)).await.is_err() {
                return;
            }
        }
        let finish = chunk(json!([{
            "index": 0,
            "delta": {},
            "finish_reason": "stop"
        }]));
        if sender.send(Ok(finish)).await.is_err() {
            return;
        }
        if include_usage {
            let usage = openai_chunk(&json!({
                "id": "chatcmpl-gwbench",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock-upstream",
                "choices": [],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": config.output_tokens,
                    "total_tokens": config.output_tokens + 1
                }
            }));
            if sender.send(Ok(usage)).await.is_err() {
                return;
            }
        }
        let _ = sender
            .send(Ok(Bytes::from_static(b"data: [DONE]\n\n")))
            .await;
    });
    ReceiverStream::new(receiver)
}

async fn chat_completions(State(config): State<MockConfig>, raw: Bytes) -> Response {
    if let Some(response) = failure_response(&config) {
        return response;
    }
    let body = parse_body(&raw);
    if body.get("stream").and_then(Value::as_bool) == Some(true) {
        let usage = include_usage(&body);
        return Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "text/event-stream")
            .body(Body::from_stream(openai_stream(config, usage)))
            .expect("stream response builds");
    }
    tokio::time::sleep(config.non_stream_delay()).await;
    Json(openai_body(&config)).into_response()
}
