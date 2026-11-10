# Custom k6 with the xk6-sse extension (Server-Sent Events), built with the
# official xk6 builder. All versions are pinned and the extension is compiled in
# at build time: nothing is downloaded when a test runs.
#
#   docker build -t gwbench/k6:2.3.0-sse0.2.0 k6        (or: make k6-build)
#
# xk6-sse v0.2.0 is written against k6 v2 (go.k6.io/k6/v2), so k6 must be v2.x.
# The ARGs are named *_TAG on purpose: xk6 reads a K6_VERSION env var itself and
# fails with "k6 version was specified with both flag and argument".
ARG XK6_TAG=1.4.15
ARG K6_TAG=2.3.0
ARG SSE_TAG=v0.2.0

FROM grafana/xk6:${XK6_TAG} AS builder
ARG K6_TAG
ARG SSE_TAG
RUN xk6 build "v${K6_TAG}" \
      --with "github.com/phymbert/xk6-sse@${SSE_TAG}" \
      --output /tmp/k6

# Same runtime image as the stock k6 release, with the binary swapped for ours.
FROM grafana/k6:${K6_TAG}
COPY --from=builder /tmp/k6 /usr/bin/k6
