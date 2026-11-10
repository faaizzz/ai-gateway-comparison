# Mock upstream for the gateway benchmark. Multi-stage, builds natively on arm64.
#   docker build -t gwbench/mock-llm:0.1.0 mock-llm
FROM rust:1.90-slim-bookworm AS builder
WORKDIR /src

# Build dependencies first so source edits do not rebuild them.
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && cargo build --release --locked \
    && rm -rf src target/release/mock-upstream target/release/deps/mock_upstream*

COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=builder /src/target/release/mock-upstream /usr/local/bin/mock-upstream
COPY LICENSE NOTICE /licenses/mock-llm/

ENV MOCK_HOST=0.0.0.0 \
    MOCK_PORT=9000
EXPOSE 9000
USER 65532:65532

# No curl in the image: the binary probes its own /health.
HEALTHCHECK --interval=2s --timeout=3s --start-period=1s --retries=15 \
    CMD ["mock-upstream", "--healthcheck"]

ENTRYPOINT ["mock-upstream"]
