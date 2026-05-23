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

---

## 2026-05-23 — iteration 2: NAG + post-VAE upscale chain (REJECTED)

- **Trigger:** PO observation that some baseline outputs appeared soft/blurry (snow biome especially); proposed adding an upscale model to the workflow to sharpen detail.
- **Change vs prior:** `workflows/flux_gguf_nag_upscale.json` adds 3 nodes after `VAEDecode` — `UpscaleModelLoader` (`2x-AnimeSharpV4_Fast_RCAN_PU.safetensors`) → `ImageUpscaleWithModel` (1024→2048) → `ImageScale` lanczos (2048→1024). Gen-time chain (NAG params, LoRA, prompt, CFG, sampler, seeds, steps) unchanged. New model alias `flux1-dev-q8-tree-nag-upscale`.
- **Pack:** `experiments/tmp_009/nag-upscale-v1-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4 (post-process change + first-time variant — both mandatory triggers).
- **Image paths:** `outputs/spike-strict-prompt-upscale/<biome>/bush/alpine_dwarf_shrub_cluster__1024_1024__s<seed>.png`

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos_rift | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | Reads more as tree with trunk/roots; faint soil shadow under base |
| chaos_rift | 202 | ✅ | ✅ | ❌ | ✅ | 0 | Clear soil/grass-tuft patch baked under shrub |
| chaos_rift | 303 | ✅ | ⚠ | ✅ | ✅ | 1 | Fern/frond silhouette, not rounded leafy mass; clean bg |
| grassland | 101 | ✅ | ⚠ | ✅ | ✅ | 1 | Reads as small tree with visible trunk; bg clean |
| grassland | 202 | ✅ | ✅ | ❌ | ✅ | 0 | Full iso ground platform with grass/soil rendered |
| grassland | 303 | ✅ | ✅ | ❌ | ✅ | 0 | Soil mound with pebbles baked under shrub |
| snow_frost | 101 | ❌ | ❌ | ❌ | ✅ | 0 | Snow-covered rock outcrop, no shrub depicted |
| snow_frost | 202 | ✅ | ✅ | ⚠ | ✅ | 1 | Frosted shrub; small snow/grass tuft clings at base |
| snow_frost | 303 | ❌ | ❌ | ❌ | ✅ | 0 | Conifer trees on iso snow platform — wrong subject + ground |

**Aggregate:**
- C1 subject-present: 7/9 (baseline was 8/9 — slight regression: snow_frost s303 went from partial-shrub to conifer)
- C2 type-right (✅ only): 4/9 (unchanged vs baseline)
- C3 no-ground (✅ only): 2/9 (baseline 0/9 — small uplift but still bad)
- C4 palette match (✅ only): 9/9 (baseline 8/9 — +1)
- **Verdict distribution: 0 clean / 4 partial / 5 fail** (baseline: 0 / 3 / 6 — net 1 fail downgraded to partial)

**Per-biome verdict counts:**
- chaos_rift: 0 / 2 / 1 (baseline: 0 / 1 / 2 — +1 partial)
- grassland_temperate: 0 / 1 / 2 (unchanged)
- snow_frost: 0 / 1 / 2 (unchanged)

**Sharpness comparison vs baseline (paired check on chaos s101 + chaos s202 + grassland s202 + snow s202):**

> Paired images look essentially identical — same composition, same colour, same edge softness on the leaf clusters, same painterly look on bark and snow surfaces. No meaningful sharpness uplift on any of the four pairs: leaf cluster outlines equally soft, bark grooves similarly indistinct, snow frost crystals show no extra micro-detail. No over-sharpening halos or ringing artifacts either. **Practically: about-same. If the upscale chain ran, its effect is below visual threshold at the 1024 final resolution.**
> — sub-agent verbatim

This is a real technical finding. The upscale-then-downsample chain (1024 → 2048 → 1024 lanczos) is **architecturally wrong** for the sharpness goal: the upscale model adds learned detail at 2048, then lanczos downsample to 1024 throws most of that detail away. Equivalent to no upscale. To actually preserve the detail we'd need to either output at 2048 (don't downsample) or use a HiRes Fix pattern (latent upscale + 2nd-pass KSampler at low denoise that BAKES the new detail at the higher resolution).

**Cost regression — significant:** baseline batch = 5m41s for 6 new imgs (~57 s/img). Upscale batch = 26m36s for 9 imgs (~177 s/img). ~3× gen time, traceable to upscale-model first-load overhead + per-image upscale step at 2048. Full 2445-image bundle projection rises from ~39 GPU-h to ~118 GPU-h.

**Delta vs baseline:**
- Quality: essentially unchanged (small +1 partial vs +1 fail trade, within noise on n=9)
- Sharpness: NOT improved (sub-agent: below visual threshold)
- Cost: WORSE by ~3×
- Net: **regression**

**Recommendation: REJECT upscale variant.** Do not promote `flux1-dev-q8-tree-nag-upscale` to default. The post-VAE upscale approach as configured cannot deliver the sharpness goal; the deeper issue (baked-in ground on C3) is unaffected by post-processing sharpening anyway. Sub-agent's direct recommendation: "redirect effort to prompt constraints and background masking rather than further upscaler tuning."

**Next iteration priority (revised after this finding):**
1. **Biome-specific hardened negatives + nag_scale tune** — the real C3 lever per debt #4. Try `nag_scale=7` plus snow-biome-specific anti-snow-patch terms in the negative for snow_frost, anti-iso-platform terms for grassland. One variable per iteration: start with `nag_scale=7` everywhere, holding negatives constant. (Cheap: ~9 min gen + review.)
2. **Subject-prompt tightening (C2 fix)** — drop "evergreen" (model interprets as tree) from `alpine_dwarf_shrub_cluster` prompt_subject; replace with explicit "low rounded shrub mass, no trunk, ground-level cluster". Separate variant. (Cheap.)
3. **HiRes Fix variant** *(if sharpness ever becomes a blocker)* — latent upscale 1.25× + 2nd KSampler low-denoise pass. Pricier than (1) and (2), defer until justified.
4. **SAM2 spike (TMP-ASSET-Q11)** — semantic prop-vs-platform separation. Reserved for when gen-side iterations plateau and we still have C3 failures we cannot fix at gen-time.

Upscale variant artifacts (`workflows/flux_gguf_nag_upscale.json`, `experiments/tmp_009/flux_gguf_nag_upscale.workflow.json`, `experiments/tmp_009/nag-upscale-v1-bush-spike-pack.json`, alias `flux1-dev-q8-tree-nag-upscale`) are kept in the repo as the experimental record but should not be the basis for any production decision.
