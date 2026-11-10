// Smoke test for any target, run by scripts/run.sh before the measurement.
// One request path check: a health URL (optional) and a chat completion that must
// come back with the mock's answer. Exits non-zero on failure.
//
//   TARGET_URL, MODEL, API_KEY   see lib/env.js
//   HEALTH_URL    optional GET endpoint that must answer 200
//   SMOKE_REQUESTS  number of chat requests, default 3
import http from 'k6/http';
import { check } from 'k6';
import { API_KEY, MODEL, TARGET_URL } from './lib/env.js';
import { chatBody } from './lib/payload.js';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: { checks: ['rate==1'] },
  // no setup or summary noise for a smoke test
  summaryTrendStats: ['med'],
};

export default function () {
  if (__ENV.HEALTH_URL) {
    const h = http.get(__ENV.HEALTH_URL, { timeout: '5s' });
    check(h, { 'health: status 200': (r) => r.status === 200 });
  }
  const n = Number(__ENV.SMOKE_REQUESTS || 3);
  for (let i = 0; i < n; i++) {
    const res = http.post(`${TARGET_URL}/chat/completions`, chatBody({ model: MODEL }), {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` },
      timeout: '10s',
    });
    check(res, {
      'chat: status 200': (r) => r.status === 200,
      // The mock always answers with "token token ..." (see mock-llm/README.md),
      // so this proves the request reached the mock.
      'chat: mock answer': (r) => r.status === 200 && r.json('choices.0.message.content').indexOf('token') === 0,
      'chat: usage present': (r) => r.status === 200 && r.json('usage.completion_tokens') > 0,
    });
    if (res.status !== 200) console.error(`chat status ${res.status}: ${String(res.body).slice(0, 300)}`);
  }
}
