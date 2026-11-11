// S9 soak: one fixed open-loop rate (FIXED_RATE, 100 RPS, non-streaming) for six windows of
// 300 s each (30 minutes), named w1 to w6, so k6 writes a p50/p99 sub-metric per five-minute
// window and the drift is visible. Memory growth comes from resources.csv (the sampler runs the
// whole time). `--duration` shortens the windows for smoke tests.
import { chatOnce } from '../lib/chat.js';
import { arrivalOptions, buildPlan, logPlan, tagStep } from '../lib/arrival.js';
import { FIXED_RATE, SOAK_WINDOWS } from '../lib/ladder.js';

const plan = buildPlan({
  defaultRates: SOAK_WINDOWS.map(() => FIXED_RATE),
  stepNames: SOAK_WINDOWS,
  defaultStepS: 300,
  defaultWarmupS: 15,
});
export const options = arrivalOptions(plan);

export function setup() {
  logPlan(plan);
}

export default function () {
  tagStep(plan);
  chatOnce();
}
