// S5 payload sweep: request bodies of about 1 KB, 10 KB and 100 KB at one fixed open-loop
// rate (FIXED_RATE, D27), non-streaming, mock at MOCK_LATENCY_MS=60. The prompt text is padded
// (k6/lib/payload.js); the answer is the mock's fixed 40 tokens. One measured step per size
// (b1k, b10k, b100k), same arrival rate in each, so only the body size changes.
// The response-size variant is s5_response.js.
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { FIXED_RATE, PAYLOAD_BYTES, PAYLOAD_NAMES } from '../lib/ladder.js';
import { MODEL } from '../lib/env.js';
import { chatBodyOfSize } from '../lib/payload.js';
import exec from 'k6/execution';

const plan = buildPlan({
  defaultRates: PAYLOAD_BYTES.map(() => FIXED_RATE),
  stepNames: PAYLOAD_NAMES,
  stepInfo: PAYLOAD_BYTES.map((bytes) => ({ request_bytes: bytes })),
});
export const options = arrivalOptions(plan);

export function setup() {
  logPlan(plan);
}

// Bodies are built when first needed and kept per VU (about 112 KB per VU in all).
const bodies = {};

function bodyFor(bytes) {
  if (!bodies[bytes]) bodies[bytes] = chatBodyOfSize({ model: MODEL, bytes });
  return bodies[bytes];
}

export default function () {
  tagStep(plan);
  const step = plan.steps.filter((s) => s.name === exec.vu.tags.step)[0];
  // warm-up and ramp use the first size
  const bytes = step && step.request_bytes ? step.request_bytes : PAYLOAD_BYTES[0];
  chatOnce({ body: bodyFor(bytes) });
}
