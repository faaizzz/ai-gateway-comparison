// Constant-arrival-rate steps (open loop, contract rule 8) with a warm-up that
// is excluded from the measurement.
//
// k6 pre-allocates the VUs of every scenario up front, so a chain of
// constant-arrival-rate scenarios would multiply the VU count. Instead one
// `ramping-arrival-rate` scenario walks through the steps, and every iteration
// tags its metrics with the step it started in (VU tag `step`):
//
//   step=warmup   the warm-up at the first rate; excluded from results
//   step=ramp     the 1 s change between two rates; excluded from results
//   step=r<rate>  the measured window of one step, e.g. step=r500
//
// Each measured step gets a (never-failing) threshold per metric so that k6
// writes a sub-metric such as `http_req_duration{step:r500}` into summary.json.
// Always read results from those, never from the whole-run totals, which
// include warm-up and ramp.
//
// Environment (the runner sets these from its flags; all optional):
//   RATES         comma list of requests per second, default from the script
//   STEP_S        seconds per measured step
//   WARMUP_S      warm-up seconds, at the first rate
//   PRE_VUS, MAX_VUS   VU pool; defaults scale with the highest rate
import exec from 'k6/execution';
import { envList, envNumber, GLOBAL_TAGS } from './env.js';

const RAMP_S = 1;
// Metric -> a threshold that always passes. Only the sub-metric matters.
const SUB_METRICS = {
  http_req_duration: 'max>=0',
  http_req_waiting: 'max>=0',
  http_req_failed: 'rate>=0',
  http_reqs: 'count>=0',
  checks: 'rate>=0',
  // Gateway-reported overhead (k6/lib/chat.js); has samples only for gateways that send it.
  gateway_overhead_ms: 'max>=0',
};

export function buildPlan({ defaultRates, defaultStepS = 60, defaultWarmupS = 15 }) {
  const rates = envList('RATES', defaultRates);
  if (rates.length === 0) throw new Error('RATES is empty');
  const stepS = envNumber('STEP_S', defaultStepS);
  const warmupS = envNumber('WARMUP_S', defaultWarmupS);
  const maxRate = Math.max.apply(null, rates);

  const stages = [];
  const steps = [];
  let t = 0; // seconds since the scenario started
  if (warmupS > 0) {
    stages.push({ target: rates[0], duration: `${warmupS}s` });
    steps.push({ name: 'warmup', phase: 'warmup', rate: rates[0], fromS: 0, toS: warmupS });
    t = warmupS;
  }
  rates.forEach((rate) => {
    stages.push({ target: rate, duration: `${RAMP_S}s` });
    steps.push({ name: 'ramp', phase: 'ramp', rate, fromS: t, toS: t + RAMP_S });
    t += RAMP_S;
    stages.push({ target: rate, duration: `${stepS}s` });
    steps.push({ name: `r${rate}`, phase: 'measure', rate, fromS: t, toS: t + stepS });
    t += stepS;
  });

  return {
    rates,
    stepS,
    warmupS,
    rampS: RAMP_S,
    totalS: t,
    startRate: rates[0],
    preVUs: envNumber('PRE_VUS', Math.ceil(maxRate * 0.15) + 10),
    maxVUs: envNumber('MAX_VUS', Math.ceil(maxRate) + 100),
    stages,
    steps,
  };
}

export function arrivalOptions(plan) {
  const thresholds = {};
  plan.steps
    .filter((s) => s.phase === 'measure')
    .forEach((s) => {
      Object.keys(SUB_METRICS).forEach((m) => {
        thresholds[`${m}{step:${s.name}}`] = [SUB_METRICS[m]];
      });
    });
  return {
    scenarios: {
      steps: {
        executor: 'ramping-arrival-rate',
        startRate: plan.startRate,
        timeUnit: '1s',
        preAllocatedVUs: plan.preVUs,
        maxVUs: plan.maxVUs,
        stages: plan.stages,
        gracefulStop: '10s',
      },
    },
    tags: GLOBAL_TAGS,
    thresholds,
    // p(99) is not in k6's default list.
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  };
}

// Call first thing in the iteration function: tags everything this VU records
// from now on with the step the iteration started in.
export function tagStep(plan) {
  const elapsedS = (Date.now() - exec.scenario.startTime) / 1000;
  let step = plan.steps[plan.steps.length - 1];
  for (let i = 0; i < plan.steps.length; i++) {
    if (elapsedS < plan.steps[i].toS) {
      step = plan.steps[i];
      break;
    }
  }
  exec.vu.tags.step = step.name;
  exec.vu.tags.phase = step.phase;
}

// For setup(): prints the plan with the absolute start time so the runner can
// store it and line the resource CSV up with the steps.
export function logPlan(plan) {
  console.log(
    'GWBENCH_PLAN ' +
      JSON.stringify({
        t0_ms: Date.now(),
        rates: plan.rates,
        step_s: plan.stepS,
        warmup_s: plan.warmupS,
        ramp_s: plan.rampS,
        total_s: plan.totalS,
        steps: plan.steps,
      }),
  );
}
