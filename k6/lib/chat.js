// One non-streaming chat completion against the target, with checks.
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { API_KEY, MODEL, REQUEST_TIMEOUT, TARGET_URL } from './env.js';
import { chatBody } from './payload.js';

// Gateway-reported overhead (contract rule 3: label it as gateway-reported, never
// subtract it from or compare it to k6's own numbers as if it were the same thing).
// LiteLLM sends the time it added, in ms, in `x-litellm-overhead-duration-ms`. The
// metric only gets samples when the header is present, so it stays empty for
// direct and for gateways that send no such header.
export const gatewayOverhead = new Trend('gateway_overhead_ms');
const OVERHEAD_HEADER = 'x-litellm-overhead-duration-ms';

function recordOverhead(res) {
  const h = res.headers;
  // k6 canonicalises header names ("X-Litellm-Overhead-Duration-Ms"); do not rely on it.
  const raw = h[OVERHEAD_HEADER] !== undefined ? h[OVERHEAD_HEADER] : h['X-Litellm-Overhead-Duration-Ms'];
  if (raw === undefined) return;
  const v = parseFloat(raw);
  if (isFinite(v)) gatewayOverhead.add(v);
}

const HEADERS = {
  'Content-Type': 'application/json',
  Authorization: `Bearer ${API_KEY}`,
};

// Built once per VU, not per request, so body generation costs no CPU in the loop.
let cachedBody = null;

export function chatOnce({ promptChars = 0, extraHeaders = {} } = {}) {
  if (cachedBody === null) cachedBody = chatBody({ model: MODEL, promptChars });
  const res = http.post(`${TARGET_URL}/chat/completions`, cachedBody, {
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
