// S1 baseline: k6 straight to the mock, no gateway. Open loop at the Phase 1
// rate ladder (k6/lib/ladder.js). The headline numbers every gateway is compared
// to. Also run against the mock-capacity check (S1 ramp, s1_ramp.js).
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { LADDER } from '../lib/ladder.js';

const plan = buildPlan({ defaultRates: LADDER });
export const options = arrivalOptions(plan);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
