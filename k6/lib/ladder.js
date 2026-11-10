// The Phase 1 rate ladder: requests per second used by the equal-rate scenarios
// (S1 direct, S2 through each gateway) so all targets are compared at the same
// rates. The highest rate stays at or below 1/2 to 1/3 of the measured mock
// ceiling (contract rule 9). How the numbers were chosen: docs/decisions.md D8.
// Override per run with `--rate R` (one rate) or RATES=100,200 in the environment.
export const LADDER = [100, 250, 500, 1000];

// Ramp used by S1 to find the mock ceiling while k6 shares the VM.
export const CEILING_RAMP = [1000, 2000, 4000, 8000, 12000, 16000, 20000];
