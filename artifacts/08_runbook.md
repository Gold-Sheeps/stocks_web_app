# Production Runbook

## 1) Scope
This runbook defines daily/weekly monitoring, rollback, model-update workflow, and interpretation rules for the probability API (`down/flat/up`) and action layer (`BUY/SELL/HOLD`).

## 2) Daily Checks
- Service health
  - API process alive, DB reachable, latest batch date present.
- Data freshness (DB-only)
  - `price_daily` latest date <= expected trading calendar lag.
  - Key symbols (`US:NVDA`, `US:QQQ`, `US:^SOX`) have non-stale rows.
- Output contract checks
  - `down/flat/up` are integer percentages.
  - Sum is exactly `100`.
  - `action`, `confidence`, `margin` fields present.
- Calibration safety checks
  - For fold/global temperature, verify fallback flags exist on folds (`temp_applied`, `temp_fallback_reason`).

## 3) Weekly Checks (Degradation Monitoring)
Evaluate fixed benchmark universe and fixed horizon with identical config.

Track:
- `logloss`
- `brier`
- `ece`
- action distribution (`BUY/SELL/HOLD` ratio)
- trading metrics (`trade_count`, `hit_rate`, `avg_return`, `max_drawdown`, `profit_factor`)

Alert rules (week-over-week):
- `logloss` worsens by > 3%
- OR `brier` worsens by > 3%
- OR `ece` worsens by > 10% or absolute +0.02
- OR action ratio drift exceeds configured tolerance (default 10pp)

Escalation:
- 1st alert: investigate and create incident note.
- 2 consecutive alerts: trigger rollback to last stable config/profile.

## 4 Rollback Procedure
### 4.1 Immediate Safety (same model build)
- Keep service up.
- Force conservative runtime profile:
  - `temp-scale=off` or rely on calibration fallback (Prompt4 safety valve).
  - Keep no-trade thresholds active (`HOLD` safety zone).
- Re-run benchmark backtest and confirm no further deterioration.

### 4.2 Version Rollback
- Select last known-good artifact/config hash.
- Restore previous runtime settings (including `prob_mode`, `label_mode`, `tbm_*`, thresholds).
- Redeploy and run smoke checks:
  - output contract (sum=100)
  - key metrics sanity
  - DB-only compliance

### 4.3 Post-Rollback Verification
- Confirm `logloss/brier/ece` returned within acceptable band.
- Confirm API schema compatibility and action policy outputs.
- Record rollback reason and timestamps in incident log.

## 5) Model Update Procedure (Retrain/Validate/Deploy)
1. Prepare data window and lock config.
2. Run backtest suite with baseline comparisons (prior/momentum).
3. Validate:
   - proper scoring (`logloss`, `brier`) is non-regressing vs release gate.
   - calibration (`ece`, reliability bins) acceptable.
   - fold temp fallback stats acceptable.
4. Run strategy-level checks with trading metrics.
5. Generate artifacts and review:
   - metrics JSON
   - reliability bins
   - compare reports (off vs fold)
6. Stage deployment and perform smoke tests.
7. Promote to production only after sign-off.
8. Enable heightened monitoring for first week after release.

## 6) Probability Interpretation Notes
- Probabilities are model estimates, not certainties.
- Outputs are for decision support and risk control, not guaranteed outcomes.
- `HOLD` is an intended safety action in low-confidence/no-trade regimes.
- This system is **not** investment advice and does not account for user-specific suitability, tax, or legal constraints.

## 7) Minimal Command Checklist
- Daily health/backtest smoke: run existing scripts and verify latest artifacts.
- Weekly compare:
  - off vs fold comparison report
  - review degradation thresholds and fallback usage
- Incident mode:
  - switch to conservative config
  - generate rollback evidence package
