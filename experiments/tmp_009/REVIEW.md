# TMP_009 — Iteration Review Log

Runtime log for the asset-pipeline iteration review workflow defined in TMP_009
§12 (sister design repo). Each section below records one fixture run + review.

## How to add a new entry

1. Trigger fired (§12.1) — note which (NAG params / prompt / model / post-process / fixture extension).
2. Re-gen the fixed fixture (§12.2): 1 entry × 3 biomes × 3 seeds = 9 images.
   ```bash
   API_KEY=$(grep '^API_KEYS=' .env | sed -E 's/^API_KEYS=//; s/#.*//; s/[[:space:]]+$//' | cut -d',' -f1 | xargs)
   .venv/Scripts/python.exe scripts/homm3-biome-bundle-batch.py \
     --pack experiments/tmp_009/nag-cfg1.0-bush-spike-pack.json \
     --base-url http://127.0.0.1:8700 --api-key "$API_KEY" \
     --out-dir outputs/spike-strict-prompt --resume
   ```
3. Run the review (default main Claude; cold-start sub-agent when §12.4 triggers fire).
   - Reviewer scores each image on C1–C4 + V per §12.3.
   - Output table, aggregate, per-biome counts, reviewer note.
4. Append a new dated section below with: trigger · change vs prior · reviewer · table · aggregate · delta vs prior baseline · recommendation.

## Soft caps (from §12.6)

- ≤ 30 images / iteration · ≤ 3 entries · ≤ 5 main-thread rounds before mandatory sub-agent · ≥ 45 imgs (3 entries × 5 biomes × 3 seeds) before any production rollout.

## Anti-rubber-stamp rules (from §12.5)

- Never declare "works" on n<9. ✅ requires passing the criterion ACROSS fixture, not "this one looks fine".
- Don't combine criteria. Cite image file path for every score. Use ⚠ liberally.
- Pre-commit cold-start sub-agent review for any change the main thread proposes to commit.

---

## 2026-05-23 — baseline (NAG + cfg=1.0 + hardened negative + composite)

- **Trigger:** initial validation of the NAG-cfg1.0 pipeline (TMP_009 §3.1).
- **Change vs prior:** N/A (this is the baseline establishment run).
- **Pack:** `experiments/tmp_009/nag-cfg1.0-bush-spike-pack.json`
- **Model alias:** `flux1-dev-q8-tree-nag` → `workflows/flux_gguf_nag.json`
- **NAG params:** `nag_scale=5.0, nag_alpha=0.5, nag_tau=1.5`
- **LoRA:** `flux/dark_fantasy_digital_v11 @ 0.8`
- **CFG / steps / sampler:** `1.0 / 28 / euler / simple`
- **Reviewer:** **cold-start sub-agent** (general-purpose, fresh context, no iteration narrative). Mandatory per §12.4 (first evaluation of a new fixture).
- **Image paths:** `outputs/spike-strict-prompt/<biome>/bush/alpine_dwarf_shrub_cluster__1024_1024__s<seed>.png`

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos_rift | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | Tree-like silhouette with exposed roots; faint soil scuff at base. |
| chaos_rift | 202 | ✅ | ✅ | ❌ | ✅ | 0 | Clear soil/grass-tuft patch baked under shrub. |
| chaos_rift | 303 | ✅ | ❌ | ❌ | ✅ | 0 | Cluster of conifer trees on rendered ground disk, not a dwarf shrub. |
| grassland | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | Reads more tree-shaped with visible trunk; subtle ground smudge. |
| grassland | 202 | ✅ | ✅ | ❌ | ✅ | 0 | Full iso platform with grass tufts and soil edge baked in. |
| grassland | 303 | ✅ | ✅ | ❌ | ✅ | 0 | Mossy soil patch with pebbles depicted under shrub. |
| snow_frost | 101 | ❌ | ❌ | ❌ | ✅ | 0 | Snow-covered rock pile, no plant present. |
| snow_frost | 202 | ✅ | ✅ | ⚠ | ✅ | 1 | Frosted shrub good; small grass tufts + pebbles cling at base. |
| snow_frost | 303 | ✅ | ⚠ | ❌ | ⚠ | 0 | Conifer trees on a full iso snow disk; green needles not frost-pale. |

**Aggregate:**
- C1 subject-present: 8/9
- C2 type-right (✅ only): 4/9
- C3 no-ground (✅ only): **0/9**
- C4 palette match (✅ only): 8/9
- Verdict distribution: **0 clean / 3 partial / 6 fail**

**Per-biome verdict counts:**
- chaos_rift: 0 / 1 / 2
- grassland_temperate: 0 / 1 / 2
- snow_frost: 0 / 1 / 2

**Reviewer note:** Worst systemic failure is **C3 — not a single image lands a transparent/white base**; every sprite bakes in soil, grass tufts, pebbles, or a full iso ground disk, which will clash with the canonical green diamond tile. Secondary issue is subject drift: chaos s303 and snow s303 render conifer-tree clusters, and snow s101 renders only a rock pile with no plant at all. Palette discipline is the one bright spot.

**Delta vs prior baseline:** N/A (first run).

**Meta-note (main thread):** The main thread's earlier self-review had scored this fixture 4 clean / 2 partial / 3 fail — significantly more lenient. The divergence was on C3 specifically: main thread was reading C3 *relative to the original homm3-bundle baseline* (NAG is clearly better than nothing); sub-agent applied C3 *absolutely* (any non-white pixel under the shrub fails). The absolute reading is correct for downstream post-process: RMBG keeps non-white pixels, so "faint soil scuff" survives the cutout and shows up on the canonical tile. The §12 cold-start review caught this calibration drift on its first run — direct payoff for the workflow.

**Recommendation:**
- **NOT production-ready.** Do not roll NAG-at-cfg1.0 into the full bundle as configured.
- **Next step priority** (cheapest-first):
  1. Tune `nag_scale` upward (try 7, 9) on the same fixture — does stronger guidance push C3 above 0/9?
  2. If (1) plateaus, try biome-specific hardened negatives (snow_frost gets stronger anti-snow-patch terms; grassland gets stronger anti-iso-platform terms).
  3. If gen-side still ≤ 4/9 on C3, escalate to TMP-ASSET-Q11 (SAM2 text-prompted semantic separation in post-process).
- **Before any of those is committed:** §12.4 cold-start sub-agent review again. The main thread cannot self-approve another iteration of this same change.
