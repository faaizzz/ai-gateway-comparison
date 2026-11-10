// Settings every scenario reads from the environment. The runner
// (scripts/run.sh) sets them from the target's gateways/<name>/target.env and
// from its own flags; a script can also be run by hand with `-e KEY=value`.
//
//   TARGET_URL        OpenAI-style base URL as seen from the k6 container
//                     (the "/v1" prefix included), e.g. http://mock-llm:9000/v1
//   MODEL             model name sent in the request body
//   API_KEY           bearer token sent to the target
//   BENCH_TARGET      target name for tags (direct, litellm, ...)
//   BENCH_PROFILE     profile name for tags (none, minimal, prod)
//   BENCH_SCENARIO    scenario id for tags (s1, s2, ...)
//   REQUEST_TIMEOUT   per-request timeout, default 10s

export const TARGET_URL = (__ENV.TARGET_URL || 'http://mock-llm:9000/v1').replace(/\/+$/, '');
export const MODEL = __ENV.MODEL || 'mock-upstream';
export const API_KEY = __ENV.API_KEY || 'sk-gwbench';
export const REQUEST_TIMEOUT = __ENV.REQUEST_TIMEOUT || '10s';

// Tags put on every metric. `scenario` is not used because k6 already uses that
// tag for the name of the executor scenario, so the benchmark scenario id is
// `bench_scenario`.
export const GLOBAL_TAGS = {
  target: __ENV.BENCH_TARGET || 'unknown',
  profile: __ENV.BENCH_PROFILE || 'unknown',
  bench_scenario: __ENV.BENCH_SCENARIO || 'unknown',
};

export function envNumber(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  const n = Number(raw);
  if (!isFinite(n)) throw new Error(`${name} must be a number, got "${raw}"`);
  return n;
}

export function envList(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  return raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => isFinite(n) && n > 0);
}
