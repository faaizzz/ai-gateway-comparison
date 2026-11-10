// One non-streaming chat completion against the target, with checks.
import http from 'k6/http';
import { check } from 'k6';
import { API_KEY, MODEL, REQUEST_TIMEOUT, TARGET_URL } from './env.js';
import { chatBody } from './payload.js';

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
  return res;
}
