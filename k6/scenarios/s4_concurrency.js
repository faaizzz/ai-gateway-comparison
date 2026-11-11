// S4 concurrency sweep: CLOSED LOOP on purpose. A fixed number of virtual users each send a
// request, wait for the answer and send the next at once, so the offered rate depends on the
// latency (contract rule 8: not a headline number). Levels: 1, 10, 100, 1000 virtual users
// (k6/lib/ladder.js CONCURRENCY). Non-streaming, mock at MOCK_LATENCY_MS=60.
// Per-step sub-metrics are v<n>; read achieved requests per second, not a target.
import { chatOnce } from '../lib/chat.js';
import { tagStep, logPlan } from '../lib/arrival.js';
import { buildClosedPlan, closedOptions } from '../lib/closed.js';
import { CONCURRENCY } from '../lib/ladder.js';

const plan = buildClosedPlan({ defaultLevels: CONCURRENCY });
export const options = closedOptions(plan);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
