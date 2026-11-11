// One non-streaming chat completion against the target, with checks.
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { API_KEY, MODEL, REQUEST_TIMEOUT, TARGET_URL } from './env.js';
import { chatBody } from './payload.js';

// Gateway-reported overhead (contract rule 3: label it as gateway-reported, never
// subtract it from or compare it to k6's own numbers as if it were the same thing).
// The metric only gets samples when the header is present, so it stays empty for
// direct and for gateways that send no such header.
//  - LiteLLM sends the time it added, in ms, in `x-litellm-overhead-duration-ms`.
//  - Bifrost sends no overhead, only the time its request spent blocked on the
//    upstream, `x-bifrost-upstream-latency-ms`. Its docs say to subtract that from the
//    caller's own elapsed time to get what Bifrost cost, so the sample is this one
//    request's k6 duration minus the header. That is a per-request difference (not a
//    difference of percentiles) and it includes the k6 to gateway hop (decision D13).
export const gatewayOverhead = new Trend('gateway_overhead_ms');
const OVERHEAD_HEADER = 'x-litellm-overhead-duration-ms';
const UPSTREAM_HEADER = 'x-bifrost-upstream-latency-ms';

// k6 canonicalises header names ("X-Litellm-Overhead-Duration-Ms"); do not rely on it.
function headerValue(h, name) {
  if (h[name] !== undefined) return h[name];
  const canon = name.replace(/(^|-)([a-z])/g, (m, a, b) => a + b.toUpperCase());
  return h[canon];
}

function recordOverhead(res) {
  const h = res.headers;
  const own = parseFloat(headerValue(h, OVERHEAD_HEADER));
  if (isFinite(own)) {
    gatewayOverhead.add(own);
    return;
  }
  const upstream = parseFloat(headerValue(h, UPSTREAM_HEADER));
  if (isFinite(upstream)) gatewayOverhead.add(Math.max(0, res.timings.duration - upstream));
}

const HEADERS = {
  'Content-Type': 'application/json',
  Authorization: `Bearer ${API_KEY}`,
};

// Built once per VU, not per request, so body generation costs no CPU in the loop.
let cachedBody = null;

// `body` (a ready JSON string) replaces the default body; S5 passes one per payload size.
export function chatOnce({ promptChars = 0, extraHeaders = {}, body = null } = {}) {
  if (body === null && cachedBody === null) cachedBody = chatBody({ model: MODEL, promptChars });
  const res = http.post(`${TARGET_URL}/chat/completions`, body !== null ? body : cachedBody, {
    headers: Object.assign({}, HEADERS, extraHeaders),
    timeout: REQUEST_TIMEOUT,
  });
  // Cheap checks on purpose (no JSON parse), so the load generator stays light.
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has completion': (r) => typeof r.body === 'string' && r.body.indexOf('"choices"') !== -1,
  });
  recordOverhead(res);
  return res;
}
