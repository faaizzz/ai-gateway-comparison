// S8 fault injection: one open-loop step at FIXED_RATE (100 RPS, non-streaming) while the mock
// answers a share of the requests with an error (MOCK_FAIL_RATE: status 500, or MOCK_429_RATE:
// status 429 with Retry-After). The runner starts the mock with the knob; the scenario ids
// s8-fail (5 % 500) and s8-429 (5 % 429) differ only in that. What the client sees:
//   http_req_failed       share of requests that did not end in a 2xx (the client-side failure rate)
//   resp_2xx / resp_429 / resp_5xx / resp_other   counters by what the client got (other = 4xx
//                         other than 429, or no response: timeout, reset)
//   ok_ms / fail_ms       duration of the successful and of the failed requests, so the retry
//                         delay (a retried request is slower) is visible next to the plain latency
// With gateway retries (the `retry` profiles) the failure rate drops and ok_ms grows a tail.
import { chatOnce } from '../lib/chat.js';
import { Counter, Trend } from 'k6/metrics';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { FIXED_RATE } from '../lib/ladder.js';

const c2xx = new Counter('resp_2xx');
const c429 = new Counter('resp_429');
const c5xx = new Counter('resp_5xx');
const cOther = new Counter('resp_other');
const okMs = new Trend('ok_ms', true);
const failMs = new Trend('fail_ms', true);

const plan = buildPlan({ defaultRates: [FIXED_RATE] });
export const options = arrivalOptions(plan, {
  resp_2xx: 'count>=0',
  resp_429: 'count>=0',
  resp_5xx: 'count>=0',
  resp_other: 'count>=0',
  ok_ms: 'max>=0',
  fail_ms: 'max>=0',
});

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  const res = chatOnce();
  const s = res.status;
  if (s >= 200 && s < 300) {
    c2xx.add(1);
    okMs.add(res.timings.duration);
    return;
  }
  if (s === 429) c429.add(1);
  else if (s >= 500 && s < 600) c5xx.add(1);
  else cOther.add(1);
  failMs.add(res.timings.duration);
}
