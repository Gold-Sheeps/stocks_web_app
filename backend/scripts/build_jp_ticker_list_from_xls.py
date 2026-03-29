"""
Build JP ticker list from an Excel .xls file.

Rule:
- Read 2nd column (index=1)
- Extract 4-digit codes
- Output as JP:xxxx (one per line, deduplicated, original order)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


def _normalize_code(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # Common Excel numeric representation like 7203.0
    if re.fullmatch(r"\d{4}\.0+", s):
        return s.split(".", 1)[0]

    # Extract strict 4-digit code token.
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", s)
    if not m:
        return None
    return m.group(1)


def build_jp_list(xls_path: Path, out_path: Path, sheet: int = 0, header: int | None = None) -> int:
    try:
        # .xls requires xlrd
        df = pd.read_excel(xls_path, sheet_name=sheet, header=header, engine="xlrd")
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'xlrd' for .xls reading. Install with: uv add xlrd"
        ) from exc

    if df.shape[1] < 2:
        raise RuntimeError(f"Input has only {df.shape[1]} columns; 2nd column is required.")

    col = df.iloc[:, 1]
    seen: set[str] = set()
    out: list[str] = []
    for v in col.tolist():
        code = _normalize_code(v)
        if not code:
            continue
        symbol_key = f"JP:{code}"
        if symbol_key in seen:
            continue
        seen.add(symbol_key)
        out.append(symbol_key)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return len(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build JP ticker list from data_j.xls column 2")
    parser.add_argument("--input", default="stocks_list/data_j.xls", help="Input .xls path")
    parser.add_argument("--output", default="backend/config/universe_jp.txt", help="Output list path")
    parser.add_argument("--sheet", type=int, default=0, help="Sheet index (default: 0)")
    parser.add_argument("--header", type=int, default=None, help="Header row index (default: None)")
    args = parser.parse_args()

    count = build_jp_list(
        xls_path=Path(args.input),
        out_path=Path(args.output),
        sheet=args.sheet,
        header=args.header,
    )
    print(f"[OK] Wrote {count} JP tickers to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
