# Baseline Report (NVDA 2026-02-16)

## Inputs
- `backtest_main`: `backend\ml_predictor_data\backtest_nvda_20260216.json`
- `backtest_off`: `backend\ml_predictor_data\backtest_nvda_20260216_off.json`
- `backtest_fold`: `backend\ml_predictor_data\backtest_nvda_20260216_fold.json`
- `compare_fast`: `backend\ml_predictor_data\temp_scale_compare_nvda_20260216.json`
- `compare_nofast`: `backend\ml_predictor_data\temp_scale_compare_nvda_20260216_nofast.json`

## Metrics Comparison

| run | accuracy | macro_f1 | logloss | brier | ece |
|---|---:|---:|---:|---:|---:|
| main | 0.4072 | 0.2997 | 1.138796 | 0.226987 | 0.092950 |
| off | 0.3982 | 0.2934 | 1.124637 | 0.225369 | 0.097652 |
| fold | 0.4072 | 0.2997 | 1.138796 | 0.226987 | 0.092950 |

## Confusion Matrix Bias
- **main** cm=[[12, 14, 154], [17, 24, 103], [16, 25, 190]] -> over=up (+0.389), under=down (-0.243); pred_share=[0.081, 0.114, 0.805] true_share=[0.324, 0.259, 0.416]
- **off** cm=[[11, 23, 146], [17, 25, 102], [15, 31, 185]] -> over=up (+0.364), under=down (-0.247); pred_share=[0.077, 0.142, 0.78] true_share=[0.324, 0.259, 0.416]
- **fold** cm=[[12, 14, 154], [17, 24, 103], [16, 25, 190]] -> over=up (+0.389), under=down (-0.243); pred_share=[0.081, 0.114, 0.805] true_share=[0.324, 0.259, 0.416]

## Practical Blockers Top 5
1. Temp fold instability: fast delta_logloss=0.0657290675239155 delta_brier=0.003678408867143601; nofast delta_logloss=0.01415895548665258 delta_brier=0.0016176705661510582 (both worse than off).
2. Objective mismatch risk: fold temp non-increasing extreme-rate but degrades proper scoring rules vs off (fast and nofast).
3. Class prediction skew (main): over=up (+0.389), under=down (-0.243); pred_share=[0.081, 0.114, 0.805] true_share=[0.324, 0.259, 0.416]
4. Class prediction skew (off): over=up (+0.364), under=down (-0.247); pred_share=[0.077, 0.142, 0.78] true_share=[0.324, 0.259, 0.416]
5. Class prediction skew (fold): over=up (+0.389), under=down (-0.243); pred_share=[0.081, 0.114, 0.805] true_share=[0.324, 0.259, 0.416]

## Temp Mode Comparison Snapshot
- fast: off(ll=1.183909012618685, br=0.23713572737367106) vs fold(ll=1.2496380801426006, br=0.24081413624081466), delta=(0.0657290675239155, 0.003678408867143601)
- nofast: off(ll=1.1246372102410753, br=0.22536927726208847) vs fold(ll=1.138796165727728, br=0.22698694782823953), delta=(0.01415895548665258, 0.0016176705661510582)