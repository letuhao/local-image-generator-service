#!/usr/bin/env python3
"""Migrate map bundle manifests from legacy `prompt` blobs to `prompt_base` (+ optional `prompt_suffix`).

Designed for map_bundles/investiture-of-the-gods: strips framing/category boilerplate already merged by
generic-bundle-batch.py decorators, removes known duplicate tails, extracts LoRA-style camera phrases.

Usage:
  python scripts/migrate-investiture-prompts.py --bundle map_bundles/investiture-of-the-gods --dry-run
  python scripts/migrate-investiture-prompts.py --bundle map_bundles/investiture-of-the-gods
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


GLOBAL_LEGACY_STRIPS: frozenset[str] = frozenset(
    {
        "isometric 2.5d game object sprite",
        "game object sprite",
        "small game prop sprite",
        "modular game environment block",
        "game creature sprite",
        "game vehicle sprite",
        "white background",
        "isolated object",
        "isolated subject",
        "isolated prop",
        "isolated segment",
        "isolated",
        "isolated on white",
        "isolated against white",
        "single sprite",
        "single focal prop",
        "single focal structure",
        "single focal building",
        "single focal artifact",
        "single focal creature",
        "single focal mount or vehicle",
        "single focal instrument",
        "single focal water prop",
        "single module focus",
        "centered composition",
        "centered prop",
        "clean alpha background",
        "clean alpha",
        "clean edges",
        "game foliage asset",
        "game foliage asset style",
        "game prop style",
        "game item sprite",
        "game item sprite style",
        "game terrain base",
        "game asset ready",
        "game asset",
        "game character base",
        "game vehicle asset",
        "flat lighting",
        "flat shading",
        "orthographic top-down tile",
        "translucent overlay tile",
        "seamless tiling",
        "seamless border",
        "vfx sprite",
        "alpha-ready",
        "no solid geometry",
        "dramatic rim light",
        "flat color blocking",
        "readable silhouette",
        "dynamic pose",
        "destructible object style",
        "isometric level design asset",
        "modular building block",
        "2.5d sprite angle",
        "2.5d orthographic view",
        "full body game character sprite",
    }
)

_REGEX_CLEANUPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r",\s*isolated on isometric 2\.5D game object sprite,\s*white background,\s*isolated object,\s*clean edges\.?",
            re.I,
        ),
        "",
    ),
    (
        re.compile(r",\s*isolated on white,\s*clean edges?", re.I),
        "",
    ),
    (
        re.compile(r"(,\s*isolated object){2,}", re.I),
        ", isolated object",
    ),
)


def _strip_set_for_category(cat_id: str, lookup: dict[str, dict]) -> set[str]:
    s = set(GLOBAL_LEGACY_STRIPS)
    info = lookup.get(cat_id) or {}
    for key in ("framing_prompt", "prompt_keywords"):
        raw = info.get(key) or ""
        for part in raw.split(","):
            t = part.strip().lower()
            if len(t) >= 2:
                s.add(t)
    return s


def _regex_preclean(prompt: str) -> str:
    p = prompt.strip()
    for rx, repl in _REGEX_CLEANUPS:
        p = rx.sub(repl, p)
    return p.strip().strip(",").strip()


def _dedupe_segments(segments: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for seg in segments:
        key = seg.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(seg.strip())
    return out


def migrate_prompt_blob(prompt: str, cat_id: str, lookup: dict[str, dict]) -> tuple[str, str | None]:
    raw = prompt.strip()
    if not raw:
        return "", None

    preclean = _regex_preclean(raw)
    segments = [s.strip() for s in preclean.split(", ") if s.strip()]
    strip_set = _strip_set_for_category(cat_id, lookup)

    suffix_parts: list[str] = []
    kept: list[str] = []
    for seg in segments:
        low = seg.lower().strip()
        if "change the camera angle" in low:
            suffix_parts.append(seg)
            continue
        if low in strip_set:
            continue
        kept.append(seg)

    kept = _dedupe_segments(kept)
    suffix_parts = _dedupe_segments(suffix_parts)

    base = ", ".join(kept)
    sfx = ", ".join(suffix_parts) if suffix_parts else None
    return base, sfx


def load_category_lookup(bundle_root: Path) -> dict[str, dict]:
    path = bundle_root / "asset_categories.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data.get("categories", [])}


def migrate_manifest(path: Path, lookup: dict[str, dict], *, dry_run: bool) -> tuple[int, list[str]]:
    """Returns (changes_count, notes)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cat_id = data.get("category") or path.parent.name
    notes: list[str] = []
    changed = 0

    assets = data.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if asset.get("prompt_base"):
                continue
            legacy = asset.get("prompt")
            if not legacy or not str(legacy).strip():
                notes.append(f"{path}: asset {asset.get('id')} missing prompt — skipped")
                continue

            base, sfx = migrate_prompt_blob(str(legacy), cat_id, lookup)
            if len(base.strip()) < 12:
                notes.append(
                    f"{path}: asset {asset.get('id')} strip produced short base; keeping regex-clean legacy only"
                )
                base = _regex_preclean(str(legacy))

            asset["prompt_base"] = base
            if sfx:
                asset["prompt_suffix"] = sfx
            del asset["prompt"]
            changed += 1

    if changed and not dry_run:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return changed, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate investiture bundle prompts to prompt_base.")
    parser.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="Bundle root containing asset_categories.json and locations/",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print counts only; do not write files")
    args = parser.parse_args()

    bundle_root = args.bundle.resolve()
    lookup = load_category_lookup(bundle_root)
    locations = bundle_root / "locations"
    if not locations.is_dir():
        print(f"No locations dir under {bundle_root}", file=sys.stderr)
        return 2

    total_changes = 0
    all_notes: list[str] = []
    for manifest in sorted(locations.rglob("manifest.json")):
        # Skip backup paths if any
        if ".bak" in manifest.parts:
            continue
        ch, notes = migrate_manifest(manifest, lookup, dry_run=args.dry_run)
        total_changes += ch
        all_notes.extend(notes)
        if ch and args.dry_run:
            print(f"[would migrate] {ch} assets in {manifest.relative_to(bundle_root)}")

    print(f"Total assets migrated: {total_changes}" + (" (dry-run)" if args.dry_run else ""))
    for n in all_notes[:50]:
        print(n, file=sys.stderr)
    if len(all_notes) > 50:
        print(f"... {len(all_notes) - 50} more notes", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
