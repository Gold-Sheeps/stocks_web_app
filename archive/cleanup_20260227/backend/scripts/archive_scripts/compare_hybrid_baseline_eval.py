from __future__ import annotations

import argparse
import json
from pathlib import Path


METRIC_KEYS = [
    "precision_at_p70",
    "coverage_at_p70",
    "n_trades_at_p70",
    "avg_return_at_p70",
    "precision_at_threshold",
    "coverage_at_threshold",
    "n_trades_at_threshold",
    "avg_return_at_threshold",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare baseline vs hybrid eval JSON under identical conditions.")
    p.add_argument("--baseline", required=True)
    p.add_argument("--hybrid", required=True)
    return p.parse_args()


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    args = _parse_args()
    b = _load_json(args.baseline)
    h = _load_json(args.hybrid)
    print("period_baseline", b.get("period"))
    print("period_hybrid", h.get("period"))
    print("run_params_baseline", (b.get("meta") or {}).get("run_params"))
    print("run_params_hybrid", (h.get("meta") or {}).get("run_params"))
    print("metrics_diff")
    for k in METRIC_KEYS:
        bv = (b.get("metrics") or {}).get(k)
        hv = (h.get("metrics") or {}).get(k)
        diff = (hv - bv) if isinstance(bv, (int, float)) and isinstance(hv, (int, float)) else None
        print(f"{k}: baseline={bv} hybrid={hv} diff={diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

