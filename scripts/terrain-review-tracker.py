#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCORE_FIELDS = (
    "genre_fit_0_2",
    "readability_0_2",
    "consistency_0_2",
    "artifact_severity_0_2",
    "prompt_responsiveness_0_2",
)


def _parse_score(raw: object, field: str, candidate_id: str) -> int:
    if raw is None:
        return 0
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip() != "":
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise ValueError(
                f"{candidate_id}: {field} must be integer in range 0..2"
            ) from exc
    else:
        return 0
    if value < 0 or value > 2:
        raise ValueError(f"{candidate_id}: {field} must be in range 0..2")
    return value


def _decision(total: int) -> str:
    if total >= 8:
        return "approved_candidate"
    if total >= 6:
        return "reviewed"
    return "rejected"


def cmd_score(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    scores = data.get("scores", [])
    updated = 0
    for row in scores:
        candidate_id = row.get("candidate_id", "<unknown>")
        # Skip untouched rows; keep total/decision unset until a reviewer enters
        # at least one explicit score field.
        has_any_score = any(
            row.get(field) not in (None, "", "null") for field in SCORE_FIELDS
        )
        if not has_any_score:
            continue
        total = sum(
            _parse_score(row.get(field), field, str(candidate_id))
            for field in SCORE_FIELDS
        )
        decision = _decision(total)
        prev_total = row.get("total_score_0_10")
        prev_decision = row.get("decision")
        row["total_score_0_10"] = total
        row["decision"] = decision
        if prev_total != total or prev_decision != decision:
            updated += 1
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Updated score decisions for {updated} row(s) in {path}")
    return 0


def cmd_validate(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = {c.get("candidate_id") for c in data.get("candidates", [])}
    if None in candidates:
        candidates.remove(None)
    terrains = set(data.get("terrainFamilies", []))

    missing = []
    for run in data.get("runs", []):
        if run.get("candidate_id") not in candidates:
            missing.append(f"unknown candidate_id: {run.get('candidate_id')}")
        if run.get("terrain_family") not in terrains:
            missing.append(f"unknown terrain_family: {run.get('terrain_family')}")
        if not run.get("output_path"):
            missing.append("run output_path is empty")

    if missing:
        print("Validation failed:")
        for issue in missing:
            print(f"- {issue}")
        return 1

    print(
        f"Validation passed for {len(data.get('runs', []))} run record(s) in {path}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage terrain review metadata score/validation steps."
    )
    parser.add_argument(
        "--file",
        default="docs/architecture/terrain-model-tracking.json",
        help="Path to terrain tracking JSON file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("score", help="Compute total_score_0_10 and decision fields.")
    sub.add_parser("validate", help="Validate run entries against known candidates.")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Tracking file not found: {path}")
    if args.command == "score":
        return cmd_score(path)
    if args.command == "validate":
        return cmd_validate(path)
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
