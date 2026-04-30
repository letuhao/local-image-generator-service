#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from pathlib import Path

SCORE_FIELDS = (
    "genre_fit_0_2",
    "readability_0_2",
    "consistency_0_2",
    "artifact_severity_0_2",
    "prompt_responsiveness_0_2",
)


def _parse_score(raw: str, field: str, row_idx: int) -> int:
    value = (raw or "").strip()
    if value == "":
        return 0
    try:
        score = int(value)
    except ValueError as exc:
        raise ValueError(f"row {row_idx}: {field} must be an integer 0..2") from exc
    if score < 0 or score > 2:
        raise ValueError(f"row {row_idx}: {field} must be in range 0..2")
    return score


def _decision(total: int) -> str:
    if total >= 8:
        return "approved_candidate"
    if total >= 6:
        return "reviewed"
    return "rejected"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate total_score_0_10 and decision fields in "
            "genre-review-scoring-template.csv rows."
        )
    )
    parser.add_argument(
        "--csv",
        default="docs/architecture/genre-review-scoring-template.csv",
        help="Path to review scoring CSV (default: %(default)s)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise SystemExit("CSV has no header row")
        rows = list(reader)

    required = set(SCORE_FIELDS) | {"total_score_0_10", "decision"}
    missing = [name for name in required if name not in fieldnames]
    if missing:
        raise SystemExit(f"CSV missing required columns: {', '.join(sorted(missing))}")

    updated = 0
    for idx, row in enumerate(rows, start=2):  # header is line 1
        total = sum(_parse_score(row.get(field, ""), field, idx) for field in SCORE_FIELDS)
        decision = _decision(total)
        prev_total = (row.get("total_score_0_10") or "").strip()
        prev_decision = (row.get("decision") or "").strip()
        row["total_score_0_10"] = str(total)
        row["decision"] = decision
        if prev_total != row["total_score_0_10"] or prev_decision != decision:
            updated += 1

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} row(s) in {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
