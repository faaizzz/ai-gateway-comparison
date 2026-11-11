// One open-loop step at FIXED_RATE (100 RPS, D28), non-streaming, mock at MOCK_LATENCY_MS=60,
// 60 s after a 15 s warm-up. Used by S6 (tracing and logging on vs off) and S7 (auth, virtual
// key, budget, rate limit vs minimal). The scenarios differ only in the target and its profile,
// which is the whole point: same traffic, a different feature set behind the gateway.
// The step is named r100 (or r<rate> with --rate), read it from summary.json as usual.
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { FIXED_RATE } from '../lib/ladder.js';

const plan = buildPlan({ defaultRates: [FIXED_RATE] });
export const options = arrivalOptions(plan);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
