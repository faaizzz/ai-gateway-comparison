// S5 response-size variant: one measured step at the fixed rate (FIXED_RATE, D27) with the small
// default request, while the mock answers with a larger fixed response. The response size is a
// mock setting (MOCK_OUTPUT_TOKENS words of "token ", 6 bytes each), so each size is its own run
// on a fresh stack: export MOCK_OUTPUT_TOKENS (1700 for about 10 KB, 17000 for about 100 KB)
// before `make run`, and use the scenario id that names it (scenarios.txt: s5-resp10k,
// s5-resp100k) so the results land in separate folders. The step is named r<rate> as in S2.
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
