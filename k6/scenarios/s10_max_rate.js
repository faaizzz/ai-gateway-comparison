// S10 max rate: an open-loop ladder (constant arrival rate, non-streaming, mock at
// MOCK_LATENCY_MS=60) that climbs until the target stops holding the rate. The runner passes the
// slice of k6/lib/ladder.js MAX_RATE_LADDER that fits the target (`--rate 1000,1500,...`), with
// 30 s steps after a 10 s warm-up. scripts/sustained.py judges the steps (D15 + D24): the result
// is the highest step before the first one that is not sustained, with k6's CPU recorded so a
// load-generator-bound step is flagged (D17). Reported separately from headline numbers.
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { MAX_RATE_LADDER } from '../lib/ladder.js';

const MAX_VUS_CAP = 5000;
const plan = buildPlan({ defaultRates: MAX_RATE_LADDER, defaultStepS: 30, defaultWarmupS: 10 });
export const options = arrivalOptions(plan);
// Cap the VU pool. A target that stops keeping up makes iterations pile up, and with the default pool
// (rate + 100 VUs, up to 16100) k6 grew to over 5 GB and was OOM-killed in the Docker VM, losing the
// whole run. At 60 ms per request 5000 VUs carry about 80000 RPS, so the cap never limits a
// healthy step; a step that needs more is not sustained anyway (k6 then drops iterations and the
// achieved rate falls under 95 %).
options.scenarios.steps.maxVUs = Math.min(plan.maxVUs, MAX_VUS_CAP);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
