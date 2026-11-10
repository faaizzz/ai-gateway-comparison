// Smoke test for the base stack: proves the custom k6 image works and can reach
// the mock with a plain request and with an SSE (streaming) request via xk6-sse.
//
//   TARGET_URL  OpenAI-style base URL, default http://mock-llm:9000/v1
//   API_KEY     bearer token sent to the target, default "sk-gwbench" (ignored by the mock)
//   DURATION    how long to run, default 5s (one VU, looped)
import http from 'k6/http';
import sse from 'k6/x/sse';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.TARGET_URL || 'http://mock-llm:9000/v1';
const KEY = __ENV.API_KEY || 'sk-gwbench';

// k6 reports no time-to-first-token for SSE (http_req_waiting is not emitted by
// xk6-sse), so time the first data event ourselves.
const ttft = new Trend('stream_ttft_ms', true);
const streamTotal = new Trend('stream_total_ms', true);

export const options = {
  vus: 1,
  duration: __ENV.DURATION || '5s',
  thresholds: {
    checks: ['rate==1'],
  },
};

const headers = {
  'Content-Type': 'application/json',
  Authorization: `Bearer ${KEY}`,
};

function body(stream) {
  return JSON.stringify({
    model: 'mock-upstream',
    stream,
    messages: [{ role: 'user', content: 'hello' }],
  });
}

export default function () {
  // 1. plain chat completion
  const res = http.post(`${BASE}/chat/completions`, body(false), { headers });
  check(res, {
    'plain: status 200': (r) => r.status === 200,
    'plain: has content': (r) => r.json('choices.0.message.content') !== '',
  });

  // 2. streaming chat completion over SSE (xk6-sse)
  let events = 0;
  let sawDone = false;
  let first = 0;
  const start = Date.now();
  const resp = sse.open(
    `${BASE}/chat/completions`,
    { method: 'POST', headers, body: body(true), tags: { name: 'chat_stream' } },
    function (client) {
      client.on('event', function (event) {
        if (events === 0) first = Date.now() - start;
        events++;
        if (event.data === '[DONE]') {
          sawDone = true;
          client.close();
        }
      });
      client.on('error', function (e) {
        console.error(`sse error: ${e.error()}`);
      });
    },
  );
  ttft.add(first);
  streamTotal.add(Date.now() - start);
  check(resp, {
    'sse: status 200': (r) => r && r.status === 200,
    'sse: got events': () => events > 1,
    'sse: got [DONE]': () => sawDone,
  });
}
