// S3 streaming: SSE chat completions through the target at fixed open-loop rates
// (STREAM_RATES = 50 and 100 per second, D27), measured with xk6-sse. Mock defaults: TTFT 20 ms, ITL 5 ms, 40 tokens
// (MOCK_OUTPUT_TOKENS; the runner passes it as EXPECT_CHUNKS).
//
// Per request:
//   stream_ttft_ms      time from sending the request to the first content delta that is not
//                       empty. The mock's first event is a role chunk with empty content, which
//                       is not counted (the response headers are flushed at once, so
//                       http_req_waiting would not be TTFT either).
//   stream_itl_ms       gap between two consecutive non-empty content deltas, one sample per
//                       gap (about 39 per request). Read p50, p99 and max of all gaps.
//   stream_max_gap_ms   the largest gap of each request (its worst stall).
//   stream_total_ms     request sent until [DONE] (or the end of the stream).
//   stream_chunks       number of non-empty content deltas received.
//   stream_faithful     rate: chunks == EXPECT_CHUNKS, text == "token " x EXPECT_CHUNKS, a
//                       finish_reason "stop" and [DONE] all arrived. A gateway that merges or
//                       splits chunks, drops text or cuts the stream fails this.
//   stream_usage        rate: the final usage chunk (completion_tokens == EXPECT_CHUNKS) arrived.
//   stream_failed       rate: no HTTP 200, a transport error or no [DONE].
// Timing uses exec.instance.currentTestRunDuration (milliseconds with a fraction; k6 has no
// global performance.now()) in the k6 JS event loop, the same for every target; it
// includes the JS hand-off, so compare against the direct run. Decision D27.
// chat.js registers gateway_overhead_ms, which the shared per-step thresholds mention
import '../lib/chat.js';
import sse from 'k6/x/sse';
import exec from 'k6/execution';
import { check } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { API_KEY, MODEL, REQUEST_TIMEOUT, TARGET_URL, envNumber } from '../lib/env.js';
import { STREAM_RATES } from '../lib/ladder.js';
import { chatBody } from '../lib/payload.js';

const EXPECT_CHUNKS = envNumber('EXPECT_CHUNKS', 40);
const EXPECT_TEXT = 'token '.repeat(EXPECT_CHUNKS);

const ttft = new Trend('stream_ttft_ms', true);
const itl = new Trend('stream_itl_ms', true);
const maxGap = new Trend('stream_max_gap_ms', true);
const total = new Trend('stream_total_ms', true);
const chunksTrend = new Trend('stream_chunks');
const faithful = new Rate('stream_faithful');
const usageOk = new Rate('stream_usage');
const failed = new Rate('stream_failed');
const streams = new Counter('streams');

const plan = buildPlan({ defaultRates: STREAM_RATES });
export const options = arrivalOptions(plan, {
  stream_ttft_ms: 'max>=0',
  stream_itl_ms: 'max>=0',
  stream_max_gap_ms: 'max>=0',
  stream_total_ms: 'max>=0',
  stream_chunks: 'max>=0',
  stream_faithful: 'rate>=0',
  stream_usage: 'rate>=0',
  stream_failed: 'rate>=0',
  streams: 'count>=0',
});

export function setup() {
  logPlan(plan);
}

function now() {
  return exec.instance.currentTestRunDuration;
}

const BODY = chatBody({ model: MODEL, stream: true });
const HEADERS = { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` };

export default function () {
  tagStep(plan);
  let first = -1;
  let last = -1;
  let worst = 0;
  let chunks = 0;
  let text = '';
  let finished = false;
  let sawDone = false;
  let sawUsage = false;
  const t0 = now();
  let tEnd = t0;
  const resp = sse.open(
    `${TARGET_URL}/chat/completions`,
    { method: 'POST', headers: HEADERS, body: BODY, timeout: REQUEST_TIMEOUT },
    function (client) {
      client.on('event', function (event) {
        const tNow = now();
        tEnd = tNow;
        if (event.data === '[DONE]') {
          sawDone = true;
          client.close();
          return;
        }
        let d = null;
        try {
          d = JSON.parse(event.data);
        } catch (e) {
          return;
        }
        if (d.usage && d.usage.completion_tokens === EXPECT_CHUNKS) sawUsage = true;
        const choice = d.choices && d.choices[0];
        if (!choice) return;
        if (choice.finish_reason === 'stop') finished = true;
        const c = choice.delta && choice.delta.content;
        if (!c) return;
        chunks++;
        text += c;
        if (first < 0) {
          first = tNow - t0;
          ttft.add(first);
        } else {
          const gap = tNow - last;
          itl.add(gap);
          if (gap > worst) worst = gap;
        }
        last = tNow;
      });
      client.on('error', function () {
        // counted below through the status and [DONE] checks
      });
    },
  );
  const ok = !!resp && resp.status === 200 && sawDone && first >= 0;
  streams.add(1);
  failed.add(!ok);
  if (ok) {
    total.add(tEnd - t0);
    maxGap.add(worst);
    chunksTrend.add(chunks);
    faithful.add(chunks === EXPECT_CHUNKS && text === EXPECT_TEXT && finished);
    usageOk.add(sawUsage);
  }
  check(resp, { 'stream status 200': (r) => !!r && r.status === 200 });
}
