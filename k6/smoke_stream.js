// Streaming smoke test for any target, run by scripts/smoke.sh: one SSE chat
// completion that must carry the mock's text and end with [DONE]. Exits non-zero
// on failure. Uses xk6-sse; see k6/smoke.js for the pattern.
//
//   TARGET_URL, MODEL, API_KEY   see lib/env.js
//   SMOKE_REQUESTS  number of streamed requests, default 2
import sse from 'k6/x/sse';
import { check } from 'k6';
import { API_KEY, MODEL, TARGET_URL } from './lib/env.js';
import { chatBody } from './lib/payload.js';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: { checks: ['rate==1'] },
  summaryTrendStats: ['med'],
};

export default function () {
  const n = Number(__ENV.SMOKE_REQUESTS || 2);
  for (let i = 0; i < n; i++) {
    let events = 0;
    let text = '';
    let sawDone = false;
    const resp = sse.open(
      `${TARGET_URL}/chat/completions`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` },
        body: chatBody({ model: MODEL, stream: true }),
        timeout: '10s',
      },
      function (client) {
        client.on('event', function (event) {
          events++;
          if (event.data === '[DONE]') {
            sawDone = true;
            client.close();
            return;
          }
          try {
            const d = JSON.parse(event.data);
            const c = d.choices && d.choices[0] && d.choices[0].delta && d.choices[0].delta.content;
            if (c) text += c;
          } catch (e) {
            // a non-JSON event is counted but adds no text
          }
        });
        client.on('error', function (e) {
          console.error(`sse error: ${e.error()}`);
        });
      },
    );
    check(resp, {
      'stream: status 200': (r) => r && r.status === 200,
      'stream: several events': () => events > 2,
      // The mock's text is "token token ...", so this proves the stream came from the mock.
      'stream: mock answer': () => text.indexOf('token') === 0,
      'stream: got [DONE]': () => sawDone,
    });
    if (!resp || resp.status !== 200) console.error(`stream status ${resp && resp.status}, events ${events}`);
  }
}
