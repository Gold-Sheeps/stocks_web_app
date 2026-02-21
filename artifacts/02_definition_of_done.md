# Definition of Done (Practical API/UX Contract)

## 1) Output Contract
- Output must include 3-class probabilities: `down`, `flat`, `up`.
- Each probability is an integer in 1% units.
- Sum must be exactly `100` at all times.
- Ordering/labels are fixed: `down/flat/up`.

## 2) Recommended Action Contract
- System must return one action: `BUY`, `SELL`, or `HOLD`.
- Action is derived from probability margin vs uncertainty band:
  - Let `edge_up = p_up - p_down`.
  - Let `edge_down = p_down - p_up`.
  - `BUY` if `edge_up >= theta_buy` and confidence gate passes.
  - `SELL` if `edge_down >= theta_sell` and confidence gate passes.
  - Otherwise `HOLD`.
- Rationale must be included with numeric evidence:
  - `p_up`, `p_down`, `p_flat`, selected thresholds, and margin values.

## 3) Safety Valve (No-Trade Zone)
- Low-confidence states must always map to `HOLD`.
- No-trade conditions (any true):
  - `max(p_up, p_down, p_flat) < c_min`
  - `abs(p_up - p_down) < delta_min`
  - calibration/risk warning flag is active (model-health guard)
- Default practical starting values:
  - `c_min = 45%`
  - `delta_min = 6%`
  - `theta_buy = theta_sell = 8%`
- Thresholds are config-driven and versioned; changes require revalidation.

## 4) Acceptance Criteria (Model Quality)
- Primary objective: improve probability quality over baseline_prior.
- Required in backtest acceptance window:
  - `logloss` relative improvement vs baseline_prior: at least `>= 1%`.
  - `brier` relative improvement vs baseline_prior: at least `>= 1%`.
  - `ece` relative improvement vs previous production model: at least `>= 5%`, or absolute `ECE <= 0.08`.
- If only one of (`logloss`, `brier`) improves, release is blocked unless risk committee exception is recorded.
- Probability sum and integer constraints are hard requirements (non-negotiable).

## 5) Weekly Degradation Monitoring Rules
- Weekly run (fixed symbol basket + fixed horizon) must track:
  - logloss, brier, ece
  - class distribution drift
  - action-rate drift (`BUY/SELL/HOLD` proportions)
- Alert thresholds (week-over-week):
  - `logloss` worsens by `> 3%` OR
  - `brier` worsens by `> 3%` OR
  - `ece` worsens by `> 10%` OR absolute increase `> 0.02`
- Two consecutive weekly alerts => auto-fallback to prior stable model profile.
- Monitoring artifacts must be persisted with timestamp and model/config hash.

## 6) Release Gate
- Ship only when all are true:
  - Output contract passes (integer 1% units, sum=100)
  - Action contract and rationale fields present
  - Safety valve behavior validated on low-confidence scenarios
  - Acceptance criteria satisfied
  - Weekly monitor job is active with rollback trigger
