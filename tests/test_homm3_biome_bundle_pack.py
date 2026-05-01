"""Pack schema smoke tests for HoMM3 biome bundle batches."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_BATCH_SCRIPT = REPO_ROOT / "scripts" / "homm3-biome-bundle-batch.py"


def _load_batch_module():
    spec = importlib.util.spec_from_file_location("homm3_biome_bundle_batch", _BATCH_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BATCH = _load_batch_module()


def _load_pack(name: str) -> dict:
    path = REPO_ROOT / "docs" / "architecture" / name
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _entry_allowed_for_biome(entry: dict, biome_id: str) -> bool:
    inc = entry.get("biomes_include")
    if isinstance(inc, list) and inc:
        allowed = {str(x).strip() for x in inc if str(x).strip()}
        return biome_id in allowed
    exc = entry.get("biomes_exclude")
    if isinstance(exc, list) and exc:
        banned = {str(x).strip() for x in exc if str(x).strip()}
        return biome_id not in banned
    return True


def _scenario_count_matrix(pack: dict, entries_key: str) -> int:
    biomes = [str(b["id"]).strip() for b in pack["biomes"] if str(b.get("id", "")).strip()]
    entries = pack[entries_key]
    seeds = pack["seeds"]
    pack_size = str(pack.get("size", "1024x1024"))
    n = 0
    for biome_id in biomes:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get("id", "")).strip()
            sub = str(entry.get("prompt_subject", "")).strip()
            if not eid or not sub:
                continue
            if not _entry_allowed_for_biome(entry, biome_id):
                continue
            sz_count = len(_BATCH.effective_sizes_for_entry(entry, pack_size))
            n += sz_count * len(seeds)
    return n


def test_effective_sizes_defaults_and_list():
    pack_sz = "1024x1024"
    assert _BATCH.effective_sizes_for_entry({}, pack_sz) == ["1024x1024"]
    assert _BATCH.effective_sizes_for_entry({"sizes": ["1024x1536"]}, pack_sz) == ["1024x1536"]
    assert _BATCH.effective_sizes_for_entry(
        {"sizes": ["1024x1024", "1536x1024"], "id": "x"}, pack_sz
    ) == ["1024x1024", "1536x1024"]


def test_all_homm_packs_have_fifteen_biomes():
    names = [
        "homm3-flux-terrain-biome-pack.json",
        "homm3-flux-structure-biome-pack.json",
        "homm3-flux-misc-biome-pack.json",
        "homm3-flux-bush-biome-pack.json",
        "homm3-flux-mushroom-biome-pack.json",
    ]
    expected_tail = [
        "spectral_ethereal",
        "necropolis_blight",
        "ocean_abyssal",
        "drake_badlands",
        "heaven_cloud",
        "abyss_chaos_rift",
    ]
    for name in names:
        pack = _load_pack(name)
        ids = [b["id"] for b in pack["biomes"]]
        assert len(ids) == 15, name
        assert ids[-6:] == expected_tail, name


def test_terrain_pack_placeholders_and_biomes():
    pack = _load_pack("homm3-flux-terrain-biome-pack.json")
    tpl = pack["prompt_template"]
    assert "{biome_hint}" in tpl and "{subject}" in tpl
    assert pack["lane"] == "terrain"
    biome_ids = {b["id"] for b in pack["biomes"]}
    assert "coastal_water" in biome_ids
    assert len(pack["terrain_entries"]) >= 5


def test_structure_pack_coastal_ship_restricted():
    pack = _load_pack("homm3-flux-structure-biome-pack.json")
    tpl = pack["prompt_template"]
    assert "{biome_hint}" in tpl and "{subject}" in tpl
    assert pack["lane"] == "structures"
    ship = next(e for e in pack["structure_entries"] if e["id"] == "derelict_ship_wreck_hull")
    assert ship["biomes_include"] == ["coastal_water"]
    assert _entry_allowed_for_biome(ship, "coastal_water")
    assert not _entry_allowed_for_biome(ship, "grassland_temperate")


def test_terrain_reed_includes_ocean_abyssal():
    pack = _load_pack("homm3-flux-terrain-biome-pack.json")
    reed = next(e for e in pack["terrain_entries"] if e["id"] == "dense_reed_bed_shallows")
    assert "ocean_abyssal" in reed["biomes_include"]


def test_misc_pack_lane_and_counts():
    pack = _load_pack("homm3-flux-misc-biome-pack.json")
    assert pack["lane"] == "misc"
    assert len(pack["misc_entries"]) >= 8
    assert _scenario_count_matrix(pack, "misc_entries") == 15 * 8 * 3


def test_bush_pack_lane_and_counts():
    pack = _load_pack("homm3-flux-bush-biome-pack.json")
    assert pack["lane"] == "bush"
    assert len(pack["bush_entries"]) == 6
    assert _scenario_count_matrix(pack, "bush_entries") == 15 * 6 * 3


def test_mushroom_pack_lane_and_counts():
    pack = _load_pack("homm3-flux-mushroom-biome-pack.json")
    assert pack["lane"] == "mushroom"
    assert len(pack["mushroom_entries"]) == 6
    assert _scenario_count_matrix(pack, "mushroom_entries") == 15 * 6 * 3


def test_scenario_counts_stable():
    terrain = _load_pack("homm3-flux-terrain-biome-pack.json")
    structures = _load_pack("homm3-flux-structure-biome-pack.json")
    assert _scenario_count_matrix(terrain, "terrain_entries") == 462
    assert _scenario_count_matrix(structures, "structure_entries") == 948
