// Closed-loop steps (fixed virtual users) for S4. Not for headline numbers
// (contract rule 8): in a closed loop the arrival rate depends on the latency, so a slow
// gateway is offered fewer requests, and queueing hides itself. S4 is labelled closed-loop
// everywhere it is reported.
//
// One `ramping-vus` scenario walks through the levels, like k6/lib/arrival.js does for
// rates, and every iteration tags its metrics with the step it started in:
//
//   step=warmup  the warm-up at WARMUP_VUS virtual users; excluded
//   step=ramp    the RAMP_S seconds to the next level; excluded
//   step=v<n>    the measured window of the level with n virtual users
//
// Plan steps carry `vus` (and `rate: null`) instead of a rate; tagStep and logPlan in
// arrival.js work on any plan shape.
//
// Environment (the runner sets VUS via --rate, STEP_S and WARMUP_S from its flags):
//   RATES      comma list of virtual-user counts (the flag is called --rate in the runner)
//   STEP_S     seconds per measured level
//   WARMUP_S   warm-up seconds
import { envList, envNumber, GLOBAL_TAGS } from './env.js';
import { subMetricThresholds } from './arrival.js';

const RAMP_S = 5;
const WARMUP_VUS = 10;

export function buildClosedPlan({ defaultLevels, defaultStepS = 60, defaultWarmupS = 10 }) {
  const levels = envList('RATES', defaultLevels);
  if (levels.length === 0) throw new Error('RATES (virtual-user levels) is empty');
  const stepS = envNumber('STEP_S', defaultStepS);
  const warmupS = envNumber('WARMUP_S', defaultWarmupS);
  const stages = [];
  const steps = [];
  let t = 0;
  if (warmupS > 0) {
    stages.push({ target: WARMUP_VUS, duration: `${warmupS}s` });
    steps.push({ name: 'warmup', phase: 'warmup', vus: WARMUP_VUS, rate: null, fromS: 0, toS: warmupS });
    t = warmupS;
  }
  levels.forEach((vus) => {
    stages.push({ target: vus, duration: `${RAMP_S}s` });
    steps.push({ name: 'ramp', phase: 'ramp', vus, rate: null, fromS: t, toS: t + RAMP_S });
    t += RAMP_S;
    stages.push({ target: vus, duration: `${stepS}s` });
    steps.push({ name: `v${vus}`, phase: 'measure', vus, rate: null, fromS: t, toS: t + stepS });
    t += stepS;
  });
  return { levels, stepS, warmupS, rampS: RAMP_S, totalS: t, startVUs: warmupS > 0 ? WARMUP_VUS : levels[0], stages, steps, closed_loop: true };
}

export function closedOptions(plan, extraMetrics = {}) {
  return {
    scenarios: {
      steps: {
        executor: 'ramping-vus',
        startVUs: plan.startVUs,
        stages: plan.stages,
        gracefulRampDown: '0s',
        gracefulStop: '10s',
      },
    },
    tags: GLOBAL_TAGS,
    thresholds: subMetricThresholds(plan, extraMetrics),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  };
}
