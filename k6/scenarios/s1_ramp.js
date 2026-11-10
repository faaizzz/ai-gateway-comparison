// S1 ramp: mock capacity check (contract rule 9). k6 straight to the mock at
// rising rates, short steps, while k6 itself shares the VM. The ceiling is the
// highest step where achieved requests per second still match the target, there
// are no errors or dropped iterations and p99 is still close to the baseline.
// Run it with `make run TARGET=direct SCENARIO=s1-ramp`.
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { CEILING_RAMP } from '../lib/ladder.js';

const plan = buildPlan({ defaultRates: CEILING_RAMP, defaultStepS: 15, defaultWarmupS: 10 });
export const options = arrivalOptions(plan);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
