// The Phase 1 rate ladder: requests per second used by the equal-rate scenarios
// (S1 direct, S2 through each gateway) so all targets are compared at the same
// rates. The highest rate stays at or below 1/2 to 1/3 of the measured mock
// ceiling (contract rule 9). How the numbers were chosen: docs/decisions.md D8.
// Override per run with `--rate R` (one rate) or RATES=100,200 in the environment.
export const LADDER = [100, 250, 500, 1000];

// Ramp used by S1 to find the mock ceiling while k6 shares the VM.
export const CEILING_RAMP = [1000, 2000, 4000, 8000, 12000, 16000, 20000];

// Virtual-user levels of the closed-loop concurrency sweep (S4).
export const CONCURRENCY = [1, 10, 100, 1000];

// Fixed rate of the payload sweeps (S5), in requests per second: a rate every gateway
// sustains (LiteLLM tuned: about 300 non-streaming, D25). Decision D27.
export const FIXED_RATE = 100;

// Streaming steps (S3): 50 RPS is the rate every gateway sustains (LiteLLM, tuned, holds
// about 65 streams per second and saturates at 100); 100 shows who still holds it. Decision D27.
export const STREAM_RATES = [50, 100];

// Request body sizes of S5 in bytes, and their step names.
export const PAYLOAD_BYTES = [1024, 10240, 102400];
export const PAYLOAD_NAMES = ['b1k', 'b10k', 'b100k'];

// S10 max rate (D17, D26, D28): the superset ladder in requests per second. Each target runs the
// slice around its own limit with `--rate`, always one or two steps past the first step it
// cannot hold; the sustained verdict (scripts/sustained.py) stops at the first failing step.
export const MAX_RATE_LADDER = [100, 200, 250, 300, 350, 400, 500, 750, 1000, 1250, 1500, 2000, 3000, 4000, 6000, 8000, 10000, 12000, 16000];

// Soak (S9, D28): six windows of five minutes at one fixed rate, w1 to w6. Per-window
// sub-metrics give the p99 drift over the 30 minutes.
export const SOAK_WINDOWS = ['w1', 'w2', 'w3', 'w4', 'w5', 'w6'];
