// S2 equal-rate steps: the headline scenario. The same open-loop ladder as S1
// (100, 250, 500, 1000 requests per second, non-streaming, mock at MOCK_LATENCY_MS=60)
// through each gateway, so every target is compared at equal rates (contract rule 8).
// Steps a gateway cannot hold are marked saturated afterwards by scripts/sustained.py
// (D15, D24); the ladder is not capped per gateway.
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
