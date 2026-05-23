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

---

## 2026-05-23 — iteration 3: multi-model Phase A — terrain-illustrious + LoRA stack (CATASTROPHIC FAIL)

- **Trigger:** PO direction shift (2026-05-23) — Flux too slow + plateaued quality. Pivot to multi-model pipeline using a different base for Stage 1 content gen. First candidate: `terrain-illustrious-xl-v1` (SDXL family, native CFG 5.5, terrain-tuned default negative, 26 steps vs Flux 24 — expected 3-5× speedup).
- **Change vs prior:** entire base model swapped from Flux dev Q8 → Illustrious-XL. LoRA stack also swapped (Flux-only `dark_fantasy_digital_v11` does not apply to SDXL): `game/asset/491023_isometric_isty03_style @ 0.7` (isometric composition) + `fantasy/NewFantasyCoreV4_ILL_by_VisionaryAI_ @ 0.5` (fantasy painterly). Same hardened anti-ground negative + added `body, anatomy, creature, monster, dragon` (Illustrious anti-character).
- **Pack:** `experiments/tmp_009/illustrious-static-v1-bush-spike-pack.json`
- **Workflow:** `workflows/sdxl_eps.json` (pre-existing, SDXL eps standard); LoRA injection markers verified.
- **Reviewer:** cold-start sub-agent per §12.4 (model swap = first-time variant + cost/production decision triggers — both mandatory).
- **Image paths:** `outputs/spike-illustrious-v1/<biome>/bush/alpine_dwarf_shrub_cluster__1024_1024__s<seed>.png`

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos_rift | 101 | ❌ | ❌ | ❌ | ❌ | 0 | Dense tile of anime girl faces/portraits, no shrub whatsoever. |
| chaos_rift | 202 | ❌ | ❌ | ❌ | ❌ | 0 | Pure bokeh/dot-noise field, no subject, no plant. |
| chaos_rift | 303 | ❌ | ❌ | ❌ | ❌ | 0 | Crowd of anime faces over teal wash; nothing plant-like. |
| grassland | 101 | ❌ | ❌ | ❌ | ❌ | 0 | Anime-face tile again; palette warm not green. |
| grassland | 202 | ❌ | ❌ | ❌ | ❌ | 0 | Bokeh-dot noise field; no shrub, no green. |
| grassland | 303 | ❌ | ❌ | ❌ | ❌ | 0 | Anime-face crowd on pale-blue wash, no plant. |
| snow_frost | 101 | ❌ | ❌ | ❌ | ❌ | 0 | Anime-face crowd; not pale-blue snow, not a shrub. |
| snow_frost | 202 | ❌ | ❌ | ❌ | ❌ | 0 | Bokeh-dot noise field; no subject. |
| snow_frost | 303 | ❌ | ❌ | ❌ | ❌ | 0 | Anime-face crowd; no shrub, no snow palette. |

**Aggregate:** **0/9 on every criterion. Verdict: 0 clean / 0 partial / 9 fail.**

**Dominant failure mode (sub-agent verbatim):**
> The model is not drawing shrubs at all. Seeds 101 and 303 produce dense tiled anime girl portraits/faces (clearly the Illustrious-XL anime prior dominating with no plant concept), and seed 202 produces a uniform soft-bokeh dot-noise field with no subject. The biome conditioning has zero effect — palette stays warm/neutral with cool tints, never green or violet, and there is no shrub, ground, or iso-prop structure anywhere.

**Speed measurement:** 9 imgs in 1m38s (~10.9 s/img). **~5.2× faster than the Flux baseline (~57 s/img)** — speed win confirmed even on broken output. Multi-model strategy is directionally correct; this specific execution chose the wrong base + LoRA stack.

**Root cause analysis:**
1. **`terrain-illustrious-xl-v1` is Illustrious-XL with a terrain-leaning DEFAULT negative — base model itself is anime-character-trained.** "terrain-" in the alias was misleading; the base is general-purpose Illustrious. Generated outputs collapse to its strong anime-portrait prior whenever the prompt subject is not aggressively reinforced.
2. **`NewFantasyCoreV4_ILL_by_VisionaryAI_` is a character-fantasy LoRA**, not a landscape one. "Core" + "Illustrious-tuned" + the title artifact (faces tile out) all point to character-art training. This LoRA *reinforces* the failure mode rather than countering it.
3. **`isty03_style` only shapes composition (isometric framing), not subject identity** — it cannot pull "shrub" out of a character-art base.
4. **Hardened negative ("person, human, character, face, body, anatomy, creature, monster") was insufficient** against the strength of the base + LoRA priors. SDXL negative prompts can dampen but not eliminate strong training-prior subjects.
5. **Seed 202's bokeh-noise collapse** suggests the model fell into a degenerate "no-detail safe" mode when prompt + LoRAs contradicted the base prior strongly.

**Cost:** 1m38s GPU + ~30s sub-agent review = ~2 min total spent on this iteration. Cheap fail-fast — far better than committing the wrong direction at scale.

**Delta vs baseline (NAG Flux):**
- Speed: ✅ ~5× faster (10.9s vs 57s)
- Quality: ❌ catastrophic (0/9 vs 3+ partial baseline)
- Multi-model strategy: directionally validated but execution non-viable

**Recommendation: REJECT this Phase A variant.** Do not promote `terrain-illustrious-xl-v1` or this LoRA stack for static-prop generation. The Illustrious base is wrong for non-character subjects.

**Next iteration candidates (ranked by likelihood of producing actual shrubs):**

| Candidate | Family | Why it might work better | Risk |
|---|---|---|---|
| **A.v2 — `terrain-realisticfantasy-v30`** ⭐ | SDXL | Realistic-fantasy base, not anime; "terrain" prefix is genuine here. Likely produces plant subjects when asked. | Style may be too realistic vs the HoMM3-painterly aesthetic baseline |
| A.v2 alt — `terrain-dreamshaper` | SDXL | Dreamshaper-based, broad subject coverage, less anime-biased | Less stylised than baseline |
| A.v2 alt — `terrain-sdxl-base` | SDXL | Generic SDXL terrain — safest, but maybe most generic look | May lack painterly flair |
| A.v2 alt — `chroma-hd-q8` | Chroma (different family) | Escapes both the Flux and the Illustrious traps entirely | Unknown subject behaviour; needs spike |

**LoRA stack revision for next iteration:**
- **DROP** `NewFantasyCoreV4_ILL_by_VisionaryAI_` — the smoking gun, character-art LoRA.
- **KEEP** `isty03_style` at 0.6 (composition lock; useful with any base).
- **OPTIONALLY ADD** `sdxl/df_style_v1.1 @ 0.4` IF metadata confirms it is a style LoRA, not a character LoRA. Verify before adding.
- **Or just isty03 alone** for a true "model + iso style only" measurement.

**Process note (positive):** the §12 cold-start workflow + the cheap-spike fail-fast pattern saved us. If the main thread had self-reviewed, it might have rationalised "well the bokeh ones could be late-stage if we just tune"; the cold-start sub-agent's "0/9 fail. broken" is unambiguous. The iteration cost 2 minutes total — exactly the kind of cheap signal we need to navigate the multi-model design space.

Artifacts: `experiments/tmp_009/illustrious-static-v1-bush-spike-pack.json` kept as experimental record; `outputs/spike-illustrious-v1/` kept for the visible failure modes; alias `terrain-illustrious-xl-v1` left in `config/models.yaml` (it was pre-existing).

---

## 2026-05-23 — iteration 4: Phase A.v2 — realisticfantasy-v30 + isty03 only (PARTIAL FAIL — useful signal)

- **Trigger:** Iter 3 root-cause analysis → Illustrious anime prior + character LoRA both wrong. Two-variable swap: (a) base model → `terrain-realisticfantasy-v30` (SDXL realistic-fantasy mix, not anime-prior), (b) LoRA stack → only `isty03_style @ 0.6` (dropped `NewFantasyCoreV4_ILL` — the smoking gun).
- **Change vs prior (iter 3):** Different base; dropped character LoRA; kept iso LoRA at slightly lower weight (0.7 → 0.6). Same hardened negative.
- **Pack:** `experiments/tmp_009/realisticfantasy-static-v1-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4.
- **Image paths:** `outputs/spike-realisticfantasy-v1/<biome>/bush/alpine_dwarf_shrub_cluster__1024_1024__s<seed>.png`

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos_rift | 101 | ✅ | ❌ | ❌ | ✅ | 0 | spiky violet shrub on wooden tray with cyan ground tufts; diorama |
| chaos_rift | 202 | ✅ | ❌ | ❌ | ⚠ | 0 | multi-bush grid on white iso platform with gold groundcover |
| chaos_rift | 303 | ✅ | ❌ | ❌ | ✅ | 0 | purple cauliflower-form tree on wooden tile + cyan plants + soil tufts |
| grassland | 101 | ✅ | ❌ | ❌ | ✅ | 0 | 9 conifers on grassy iso plot — full diorama; pink trees mixed in |
| grassland | 202 | ✅ | ❌ | ❌ | ⚠ | 0 | 3×3 grid of conifers/bushes on green tiles; pink trees again |
| grassland | 303 | ✅ | ❌ | ❌ | ✅ | 0 | dense pyramid stack of conifers on green iso tile |
| snow_frost | 101 | ✅ | ❌ | ❌ | ✅ | 0 | grid of cyan conifers on snow-topped wooden tray |
| snow_frost | 202 | ✅ | ❌ | ❌ | ✅ | 0 | ~12 small cyan conifers scattered on snow patch |
| snow_frost | 303 | ✅ | ⚠ | ⚠ | ✅ | 1 | single pale-blue spiky mound, mostly isolated; small snow patch under base |

**Aggregate (cold-start canonical):**
- C1 subject-present: **9/9** ⭐ (subject collapse from iter 3 GONE)
- C2 type-right (✅ only): 0/9
- C3 no-ground (✅ only): 0/9
- C4 palette match (✅ only): 6/9 (3 cases of pink/blue contamination)
- **Verdict: 0 clean / 1 partial / 8 fail** — only snow_frost s303 produced something droppable

**Per-biome:** chaos 0/0/3 · grassland 0/0/3 · snow 0/1/2

**Composition pattern (sub-agent verbatim):**
> Eight of nine are multi-object isometric dioramas — grids, clusters, or stacks of plants/trees mounted on explicit bases (wooden trays, white iso platforms, grassy plots, snow patches). The model strongly interprets "cluster" as "grid of many on a tile" rather than "one rounded shrub mass." Only snow_frost s303 produced a single isolated subject. No clear per-seed pattern; the diorama bias is consistent across all three biomes.

**Style (sub-agent verbatim):** "3D-render-clay / stylized voxel-adjacent — chunky geometric forms, plastic-clay shading, hard cast shadows. Blender clay renders of mobile-game asset packs."

**Reviewer take (verbatim):**
> Subject collapse is gone — every image renders plant-like forms — but the cluster/tile/diorama failure is now dominant and worse for tilemap use. Eight of nine are unusable as iso-tile props because they include their own ground base and multiple subjects. Only snow_frost s303 is droppable onto a tilemap. Prompt must forbid trays/platforms/grids and enforce single-subject.

**Speed:** **1m0.5s for 9 imgs = ~6.7 s/img — ~8.5× faster than Flux baseline (57 s/img).** Even faster than iter 3 (10.9s) thanks to lighter LoRA stack. Multi-model strategy speed advantage confirmed and improved.

**Root cause for the C3 + C2 failures:**

`isty03_style` is an **isometric-DIORAMA composition LoRA**, not an "isolated sprite on white BG" LoRA. Its training data encoded "isometric scene with tile/tray base + multiple objects" as the canonical output. When asked for "shrub cluster" the LoRA overrides the prompt and produces grid-of-shrubs-on-tile. The negative ("iso platform, isometric tile base, diamond base") could not counter the LoRA's positive pull.

This is **the same class of bug as iter 3** (LoRA reinforcing the wrong concept) — just a different concept. The lesson: LoRA bias is stronger than prompt for compositional concepts, even at weight 0.6.

**One critical positive signal:** `snow_frost s303` produced a *single isolated subject*. This proves the base model (`realisticFantasy_v30`) IS capable of single-isolated-prop output — the isty03 LoRA just dominates most rolls. Without the LoRA, base might produce isolated props consistently.

**Delta vs prior iterations:**
| Iter | Base | LoRA stack | Speed (s/img) | C1 | C3 | Net |
|---|---|---|:-:|:-:|:-:|---|
| 1 (NAG baseline) | Flux Q8 | dark_fantasy | 57 | 8/9 | 0/9 | 0/3/6 |
| 3 (Illustrious) | Illustrious | NewFantasyCoreV4 + isty03 | 10.9 | 0/9 | 0/9 | 0/0/9 |
| **4 (realisticfantasy)** | **realisticFantasy** | **isty03 only** | **6.7** | **9/9** | **0/9** | **0/1/8** |

Progress: subject collapse fixed (huge win). C3 still 0 but for a *different* reason (composition LoRA vs anime prior). Closer to root cause.

**Recommendation:** REJECT this exact configuration but **the realisticFantasy base is the right direction**. Next iteration must isolate base behaviour without the diorama-encoding LoRA.

**Next iteration candidates (one-variable, cheapest-first):**

| ID | Change | Hypothesis |
|---|---|---|
| **A.v3 — drop isty03 entirely** ⭐ | Same base, ZERO LoRAs | snow_frost s303 proved base alone can produce isolated props. Does base alone produce them consistently? If yes → we have a working pipeline at 0 LoRA cost. |
| A.v3-alt — try isty04 or isty02 | Same base, isty04 (or 02) instead of 03 | Maybe a different isty variant has cleaner isolated-prop training. Low confidence (all 5 isty are likely similar). |
| A.v3-alt — keep isty03 weight 0.3 + harder negative | Same base, weaken iso LoRA + add "single isolated prop NOT diorama NOT grid NOT multiple" | Maybe weight 0.6 is over; weight 0.3 may give isometric framing without the diorama bias. |
| A.v3-alt — Skip LoRA, add canny ControlNet of iso diamond outline | Use existing `noobai-xl-controlnet-canny` to force iso footprint via structural control, not LoRA bias | More setup but principled. Reserved for if drop-LoRA fails. |
| C2 fix in parallel | Drop "evergreen" from `prompt_subject` (model reads as tree) | Independent of base/LoRA. Pure prompt change. Could combine with A.v3. |

Artifacts kept as experimental record: pack JSON, outputs/spike-realisticfantasy-v1/, the `terrain-realisticfantasy-v30` alias was pre-existing.

---

## 2026-05-23 — iteration 5: Phase A.v3 — realisticfantasy + zero LoRA (REJECT — regression)

- **Trigger:** iter 4 root-cause analysis → isty03 LoRA is a diorama-composition LoRA, hence the multi-object-on-tile failure. Hypothesis: dropping the LoRA entirely will let the base model's natural single-prop behaviour surface (snow s303 in iter 4 had been the rare isolated subject, proof base is capable).
- **Change vs prior (iter 4):** drop `isty03_style` entirely → zero LoRAs. Same hardened negative + added defensive anti-diorama terms (`multiple objects, grid, diorama, scene, multiple plants, wooden tray, planter box`). Same fixture.
- **Pack:** `experiments/tmp_009/realisticfantasy-noLoRA-v1-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4.

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos | 101 | ✅ | ❌ | ❌ | ✅ | 0 | Full iso diorama platform with stone-block border, dirt path, multiple purple trees + shrub clumps |
| chaos | 202 | ✅ | ❌ | ❌ | ⚠ | 0 | Tiled/packed grid of conifer trees, teal-dominant, no isolation, no ground but reads as pattern fill |
| chaos | 303 | ✅ | ❌ | ❌ | ✅ | 0 | Single tall tree (not dwarf shrub) on circular ground patch with mountain backdrop |
| grassland | 101 | ✅ | ❌ | ❌ | ✅ | 0 | Full iso diorama: grass platform, winding path, forest of conifers + pink trees |
| grassland | 202 | ✅ | ❌ | ❌ | ✅ | 0 | Sprite sheet of ~25 separate trees/shrubs on white bg — multiple plants, not one |
| grassland | 303 | ✅ | ❌ | ❌ | ✅ | 0 | Top-down forest scene, ring of conifers around small clearing |
| snow | 101 | ✅ | ❌ | ❌ | ✅ | 0 | Cluster of ~10 separate blue conifers on snow field |
| snow | 202 | ✅ | ❌ | ❌ | ✅ | 0 | Sprite-sheet/pattern of many blue conifers across snow |
| snow | 303 | ✅ | ❌ | ❌ | ✅ | 0 | Cluster of ~10 separate blue conifers on snow field |

**Aggregate:** **0 clean / 0 partial / 9 fail.** C1 9/9 · C2 0/9 · C3 0/9 · C4 8/9.

**Composition pattern (sub-agent verbatim):**
> Dominant failure is NOT the diorama-with-tile-base pattern (only chaos s101 and grassland s101 show that). Instead the model collapses to (a) sprite-sheet of many separate plants (grassland s202, snow s101/202/303, chaos s202) and (b) full landscape/forest scenes (grassland s303, chaos s303). It also defaults to conifer/pine tree silhouettes rather than rounded dwarf shrubs across 8/9 images. Single-isolated-prop framing is absent.

**Speed:** 57s for 9 imgs = ~6.3 s/img (even faster than iter 4 without LoRA load overhead).

**Counter-intuitive finding:** removing the LoRA made the composition WORSE (0/0/9 vs iter 4's 0/1/8). The isty03 LoRA was actually helping to suppress scene/sprite-sheet mode, even though it forced tile/tray bases. Net: this iteration narrowed the hypothesis — the failure mode is **prompt-level**, not LoRA-level. The words "isometric 2.5D fantasy strategy **MAP** flora **SPRITE**" in the positive template read as "iso map game sprite sheet" not "iso single sprite".

**Recommendation: REJECT.** But the result *clarified the bottleneck* — Phase A.v4 will rewrite the positive template.

Artifacts: `experiments/tmp_009/realisticfantasy-noLoRA-v1-bush-spike-pack.json`, `outputs/spike-realisticfantasy-noLoRA-v1/`.

---

## 2026-05-23 — iteration 6: Phase A.v4 — prompt rewrite (BREAKTHROUGH — 0/9/0)

- **Trigger:** iter 5 confirmed prompt-level bottleneck. Rewrite positive template to drop "map" and aggressively demand "single specimen on white BG, no landscape, no scene, no multiple plants". Also tightened `prompt_subject` (drop "evergreen" which model reads as conifer tree; replace with "low rounded shrub mass no trunk ground-level cluster").
- **Change vs prior (iter 5):** prompt template + prompt_subject rewritten. Negative also added anti-scene/anti-sheet/anti-border terms (`forest, grove, landscape, scene, panorama, two plants, three plants, many plants, stone wall, fence, border, frame`). Same base, same zero LoRA, same fixture, same CFG.
- **Pack:** `experiments/tmp_009/realisticfantasy-promptv2-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4.

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | violet spiky bonsai-tree on top of green pine undergrowth at base |
| chaos | 202 | ✅ | ⚠ | ⚠ | ✅ | 1 | violet pom-pom tree with green spiky undergrowth tuft at base |
| chaos | 303 | ✅ | ⚠ | ⚠ | ✅ | 1 | violet spiky dome on dark spiky disk/pad — reads like second plant |
| grassland | 101 | ✅ | ⚠ | ⚠ | ⚠ | 1 | magenta/red dome above green leafy undergrowth — palette off (red not green) |
| grassland | 202 | ✅ | ⚠ | ⚠ | ⚠ | 1 | magenta pom-pom tree on green spiky base with red berries — palette mostly red |
| grassland | 303 | ✅ | ⚠ | ⚠ | ⚠ | 1 | red urchin-ball on green moss puff — palette red over green |
| snow | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | pale-blue spiky tree on green pine-needle undergrowth at base |
| snow | 202 | ✅ | ⚠ | ⚠ | ✅ | 1 | pale-blue bush on green moss mound + faint snow patch |
| snow | 303 | ✅ | ⚠ | ⚠ | ✅ | 1 | pale-blue pom-pom on green moss mound |

**Aggregate:** **0 clean / 9 partial / 0 fail** ⭐ best result across all 6 iterations.
- C1 subject-present: 9/9 ✅
- C2 type-right (✅ only): 0/9 (bonsai-tree shape pervasive; undergrowth reads as 2nd plant)
- C3 no-ground (✅ only): 0/9 (green undergrowth tuft baked at base 9/9)
- C4 palette match (✅ only): 6/9 (3 grassland imgs render magenta-on-green, palette inversion)

**Per-biome:** chaos 0/3/0 · grassland 0/3/0 · snow 0/3/0

**Composition note (sub-agent verbatim):**
> These ARE single-subject sprites on white BG. The diorama/sprite-sheet/scene failure did NOT return. No platforms, no tile borders, no forest backdrops, no grids.

**Universal secondary pattern (sub-agent verbatim):**
> Universal pattern — green spiky/leafy undergrowth tuft at the base of the main shrub in 9/9 images. Often reads as a second plant rather than as part of the main specimen. The main shrub itself trends toward a bonsai-tree silhouette (visible trunk + rounded crown) rather than a true low rounded shrub mass.

**Reviewer note (sub-agent verbatim):**
> Major improvement vs prior scene/sprite-sheet failures — isolation on white BG is solved. Remaining issue is morphological (bonsai-on-moss-puff stacking) and grassland palette inversion (red-over-green). Snow s303 and snow s202 are closest to "drop on canonical tile, looks fine." Next prompt iteration: ban undergrowth/moss/base-tuft and demand low rounded mass, not bonsai tree.

**Speed:** 54.7s for 9 imgs = ~6.1 s/img. **~9.4× faster than Flux NAG baseline (57 s/img).**

**Quality progression (recap):**

| Iter | Base / LoRA | Clean | Partial | Fail | Speed |
|---|---|:-:|:-:|:-:|:-:|
| 1 (Flux NAG baseline) | Flux Q8 / dark_fantasy | 0 | 3 | 6 | 57 s |
| 3 (Illustrious) | Illustrious / Char + iso | 0 | 0 | 9 | 11 s |
| 4 (rfantasy + isty03) | rFantasy / isty03 | 0 | 1 | 8 | 7 s |
| 5 (rfantasy zero-LoRA) | rFantasy / none | 0 | 0 | 9 | 6 s |
| **6 (prompt rewrite)** | rFantasy / none + new prompt | **0** | **9** | **0** | **6 s** |

**Net delta vs Flux NAG baseline:**
- Speed: ~9.4× faster
- Verdict: 0 outright fails (was 6) → +6 imgs not-fail
- C1 subject: 9/9 vs 8/9 → +1
- C3 absolute pass: 0/9 vs 0/9 → equal (but mode differs — Flux had baked iso platforms; rFantasy has small undergrowth tufts)
- C4 palette: 6/9 vs 8/9 → -2 (grassland palette inversion is a new problem)

**Validates the multi-model strategy on speed AND quality** (provided we solve composition at the prompt layer rather than the model layer). This is the first iteration where the multi-model pivot WINS on quality, not just speed.

**Two concrete known-issues for Phase A.v5:**

1. **Universal undergrowth pattern (9/9).** Model adds a green "base plant" beneath the requested subject. Sub-agent's prescription: "ban undergrowth/moss/base-tuft and demand low rounded mass, not bonsai tree." Add to negative: `undergrowth, moss tuft, base tuft, secondary plant, two plants, plant on top of plant, plant pile, bonsai, visible trunk, tree shape, tall trunk`.
2. **Grassland palette inversion (3/3 in grassland).** Foliage is magenta/red while undergrowth is green — model interprets "biome foliage cue accents" + "green undergrowth default" as "[biome color foliage] on top of [green undergrowth]". For grassland (whose palette IS green) the result reads "magenta-on-green" instead of "green-on-green". Fix: tighten biome hint OR remove the artificial undergrowth that's causing the layering ambiguity (both issues compound).

**Recommendation:** **STRONG PROMISE — promote to next iteration**. The prompt-bottleneck hypothesis is validated. Next variant should attack the undergrowth pattern and palette inversion via prompt-only changes (since prompt has proven to be the leverage point).

**Phase A.v5 proposal:**
- Same base (`terrain-realisticfantasy-v30`)
- Same zero LoRA
- Same `prompt_template` BUT add explicit "no second plant at base" framing
- Strengthened negative against `undergrowth, moss tuft, secondary plant, bonsai, visible trunk, two stacked plants`
- Tighten `prompt_subject` further to enforce rounded-mass-no-trunk
- Cost: ~1 min gen + sub-agent review.

If A.v5 lifts any ⚠ to ✅ on C2 or C3 → we have a working static-prop production pipeline at ~6 s/img, ~9× cheaper than Flux. That's a clear milestone to lock in.

Artifacts: `experiments/tmp_009/realisticfantasy-promptv2-bush-spike-pack.json`, `outputs/spike-realisticfantasy-promptv2/`.

---

## 2026-05-23 — iteration 7: Phase A.v5 — anti-undergrowth + anti-bonsai (REGRESSION — negation paradox)

- **Trigger:** Iter 6 sub-agent prescription "ban undergrowth/moss/base-tuft and demand low rounded mass, not bonsai tree." Plus tighten biome hints to fix the grassland palette inversion.
- **Change vs iter 6 (3 prompt-side changes bundled):**
  1. Positive added: "the entire plant is ONE unified rounded mass with NO separate undergrowth at the base, NO second plant beneath it, NO visible trunk, NO bonsai tree silhouette"
  2. Negative added: `undergrowth, moss tuft, base tuft, secondary plant, plant on top of plant, bonsai, visible trunk, tree shape, conifer, pine`
  3. Biome hints rewritten with **explicit color guidance**: chaos = "bruised violet, NO green leaves"; grassland = "all leaves green, NO red NO magenta NO pink"; snow = "pale frost-blue, NO green leaves"
- **Pack:** `experiments/tmp_009/realisticfantasy-promptv3-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4.

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos | 101 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ trunk; 2nd green-leaf undergrowth plant beneath forming ground mound |
| chaos | 202 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ exposed trunk; iso grass-disc platform w/ violet petals |
| chaos | 303 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ trunk; cave-scene bg + leafy undergrowth foreground |
| grassland | 101 | ✅ | ⚠ | ❌ | ✅ | 0 | Topiary/bonsai-tree w/ trunk; grass mound w/ flowers beneath |
| grassland | 202 | ✅ | ⚠ | ❌ | ✅ | 0 | Trunk visible; iso disc platform of leaves+flowers; teal scene bg |
| grassland | 303 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ trunk; ground patch + undergrowth foreground plants |
| snow | 101 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ trunk; leafy green undergrowth mound beneath |
| snow | 202 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree w/ trunk; small moss/leaf ground patch |
| snow | 303 | ✅ | ⚠ | ❌ | ✅ | 0 | Tree on rock/soil iso platform; undergrowth foreground leaves |

**Aggregate:** C1 9/9 · C2 0/9 · C3 0/9 · **C4 9/9 ⭐** (perfect, was 6/9 in iter 6) · Verdict **0 clean / 0 partial / 9 fail** — REGRESSION from iter 6's 0/9/0.

**Per-biome:** chaos 0/0/3 · grassland 0/0/3 · snow 0/0/3

**Failure modes (sub-agent verbatim):**
> Two dominant regressions. (1) Every image is a tree-with-visible-trunk, not a low rounded shrub — bonsai/topiary silhouette persists despite anti-bonsai prompt (all 9). (2) "Second plant beneath" failure is now near-universal: a leafy green undergrowth mound sits under the main canopy (chaos 101, grassland 101/303, snow 101/202/303). Iso platforms also returned (chaos 202, grassland 202, snow 303).

**Reviewer note (sub-agent verbatim):**
> Regression. Palette discipline held (C4 perfect, was the prior weak spot), but C2 and C3 collapsed to 0/9 — every output is a trunked tree atop an undergrowth/platform composite. The anti-undergrowth clause appears to have inverted into a reliable two-plant compositor.

**Speed:** 44.9s for 9 imgs = ~5 s/img (fastest yet, no LoRA + smaller workflow overhead).

### Lesson: negation paradox in diffusion prompts

Strongly negating a concept can paradoxically AMPLIFY it in the output. Telling the model "NO undergrowth, NO second plant beneath, NO visible trunk, NO bonsai" caused the model to render undergrowth + second plant + visible trunk + bonsai silhouette on 9/9 outputs vs iter 6's 0/9. Mechanism: the negative concept tokens (undergrowth, trunk, bonsai) still activate the corresponding visual concepts; the "NO" qualifier is weaker than the concept activation. Same lesson applies to negative prompts when over-loaded.

**What worked in iter 7 (worth backporting):** the explicit per-biome color hints. C4 went from 6/9 to 9/9 perfect with no other downside. The pattern `"<color> ... NO <wrong-color>"` worked for palette specifically — possibly because palette is a continuous attribute (less concept-activation, more attribute-binding).

**Recommendation:** REJECT iter 7. **Iter 6 remains the peak**. **Iter 8 = hybrid: iter 6 prompt + iter 7's explicit color biome hints, drop all anti-X positive/negative additions.** Target: lift C4 6/9 → 9/9 while keeping C2/C3 at iter 6's ⚠ level (no regression to ❌).

Artifacts: `experiments/tmp_009/realisticfantasy-promptv3-bush-spike-pack.json`, `outputs/spike-realisticfantasy-promptv3/`.

---

## 2026-05-23 — iteration 8: Phase A.v6 hybrid — iter 6 prompt + iter 7 color hints (REGRESSION — long-hint destabilization)

- **Trigger:** Iter 7 reverted on anti-X failure, but its explicit-color biome hints lifted C4 6/9 → 9/9 with no other downside. Hybrid hypothesis: take iter 6's working positive verbatim + iter 7's biome hints → expect 0/9/0 + lift C4.
- **Change vs iter 6:** ONLY the biome hints. Verbatim from iter 7: chaos = "bruised violet purple coloured foliage entirely, twisted thorn-knot leaves, chaos cavern palette, NO green leaves"; grassland = "healthy temperate green coloured foliage entirely, leafy bundle masses, NO red, NO magenta, NO pink, all leaves green"; snow = "pale frost-blue coloured foliage entirely, frost-rimmed dormant twigs, cold palette, NO green leaves". Positive template, prompt_subject, negative — all unchanged from iter 6.
- **Pack:** `experiments/tmp_009/realisticfantasy-promptv4-bush-spike-pack.json`
- **Reviewer:** cold-start sub-agent per §12.4.

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | Violet spiky mass on top of green undergrowth tuft (secondary plant at base) |
| chaos | 202 | ✅ | ⚠ | ⚠ | ✅ | 1 | Visible trunk + green undergrowth base; main mass violet |
| chaos | 303 | ✅ | ⚠ | ⚠ | ✅ | 1 | Small green tuft at base + cast shadow; main violet shrub OK |
| grassland | 101 | ✅ | ⚠ | ❌ | ⚠ | 0 | Tree-form w/ trunk + green grass platform/soil patch; magenta spike intrusion on crown |
| grassland | 202 | ✅ | ⚠ | ⚠ | ⚠ | 0 | Bonsai tree w/ trunk; magenta floret contamination at base |
| grassland | 303 | ✅ | ⚠ | ❌ | ✅ | 0 | Green grass mound platform under shrub; otherwise palette clean |
| snow | 101 | ✅ | ⚠ | ⚠ | ✅ | 1 | Visible trunk + green-blue undergrowth tuft; main mass pale-blue |
| snow | 202 | ✅ | ⚠ | ❌ | ✅ | 0 | Pale-blue shrub sits on mossy green/soil mound platform |
| snow | 303 | ✅ | ⚠ | ❌ | ✅ | 0 | Pale-blue shrub on soil/grass mound platform |

**Aggregate:** C1 9/9 · C2 0/9 · **C3 1/9 ✅** *(but 5 outright ❌)* · C4 7/9 · Verdict **0 clean / 4 partial / 5 fail** — REGRESSION from iter 6's 0/9/0.

**Per-biome:** chaos 0/3/0 · grassland 0/0/3 · snow 0/1/2

**Composition stability (sub-agent verbatim):**
> Failure modes returned. None are clean single-isolated subjects. Every image either depicts a ground patch/soil-mound platform (grassland 101/303, snow 202/303) or stacks a second plant/undergrowth tuft beneath the main shrub with a visible trunk between them (chaos all 3, snow 101, grassland 101/202). This is worse than a "0 fail / 9 partial" baseline — 5 outright fails on C3.

**Reviewer note (sub-agent verbatim):**
> Best-of-9: chaos seed 303 — tightest single mass, smallest ground bit, violet dominant. Still not ship-ready: tiny green tuft + cast-shadow ellipse mean it won't drop cleanly onto a canonical iso tile without masking. Zero images pass the "drop on tile, looks fine" bar.

**Speed:** 43.4s for 9 imgs = ~4.8 s/img — fastest in the spike sequence.

### Lesson: long biome hints contain implicit composition concepts

Iter 7's biome hints have *extra concept tokens* beyond pure color: "twisted thorn-knot leaves", "leafy bundle masses", "frost-rimmed dormant twigs", "leafy bundle masses". These compete with iter 6's "single specimen" framing in the positive prompt — the prompt is no longer purely a single-subject directive once the biome hint asserts plural noun phrases ("knots", "bundles", "twigs"). Result: composition stability degrades. The "NO green leaves" / "NO red" half of the hint may also tip the balance via negation-paradox at low intensity.

**For Phase B (or iter 9):** a minimal biome hint like `"violet"` / `"green"` / `"pale blue"` (just color, no plant-form descriptors) might pick up the C4 win without compositional cost — but n=8 has already shown we're at the prompt-engineering plateau. Diminishing returns likely.

### Where the spike sequence stands after 8 iterations

| Iter | Config | Speed | Clean | Partial | Fail | Verdict |
|---|---|:-:|:-:|:-:|:-:|---|
| 1 | Flux NAG baseline | 57s | 0 | 3 | 6 | partial wins, slow |
| 2 | Flux NAG + upscale | 177s | 0 | 4 | 5 | REJECT (cost+, no quality win) |
| 3 | Illustrious + char LoRA | 11s | 0 | 0 | 9 | REJECT (anime collapse) |
| 4 | rFantasy + isty03 | 7s | 0 | 1 | 8 | REJECT (diorama) |
| 5 | rFantasy zero-LoRA | 6s | 0 | 0 | 9 | REJECT (sprite-sheet/landscape) |
| **6** | **rFantasy + prompt rewrite** | **6s** | **0** | **9** | **0** | **PEAK — promoted** |
| 7 | iter 6 + anti-X clauses | 5s | 0 | 0 | 9 | REJECT (negation paradox) |
| 8 | iter 6 + iter 7 color hints | 5s | 0 | 4 | 5 | REJECT (long-hint destabilization) |

**Multi-model strategy: ~9× speed win confirmed.** Quality plateau: iter 6 `0/9/0` partial-everywhere. No iteration broke through to outright clean V=2.

**Two structural lessons from this spike sequence:**
1. **Prompt-engineering has a sweet spot beyond which more constraints regress.** Adding 1 anti-clause or 1 longer hint REGRESSES quality. We hit that sweet spot at iter 6 and over-shot at iter 7 and iter 8.
2. **Composition (C2/C3) and palette (C4) appear to be competing prompt tokens.** Each prompt-side addition that helps one tends to hurt the other.

**Recommendation:** **Lock iter 6 as the working baseline.** Decide between four next steps (see PO question after this section). The spike has produced a usable iter-6 config and a clear ceiling — no obvious additional prompt-side improvement available.

Artifacts: `experiments/tmp_009/realisticfantasy-promptv4-bush-spike-pack.json`, `outputs/spike-realisticfantasy-promptv4/`.

---

## 2026-05-23 — iteration 9: PO-request — heziUltimateJapaneseAndKorean SDXL (REJECT — character collapse, predicted)

- **Trigger:** PO suggested probing a different SDXL checkpoint (`heziUltimateJapaneseAndKorean_v10.safetensors`, 6.5GB, in `models/checkpoints/SDXL1.0/`).
- **Flagged risk before run:** name pattern ("Japanese and Korean") matches Civitai conventions for character/portrait/idol training. Iter 3 with a similar character-trained base (Illustrious-XL) had collapsed to anime faces 0/9. Expected same trap; ran anyway because cost is small (~2 min) and explicit user request.
- **Change vs iter 6:** ONLY the base checkpoint swapped. Same iter 6 prompt template + zero LoRA + same fixture + hardened negative (extended with `asian portrait, woman, man, girl, boy, korean girl, japanese girl, idol, photo` as defensive anti-character terms).
- **Pack:** `experiments/tmp_009/hezi-static-v1-bush-spike-pack.json`
- **Two operational issues hit before the gen ran:** (a) missing `vram_estimate_gb` in my models.yaml entry caused service startup to fail with `registry_validation_failed`; added `vram_estimate_gb: 8`. (b) registry parser strips checkpoint subdir via `_basename()` → ComfyUI couldn't find `heziUltimateJapaneseAndKorean_v10.safetensors` because file was at `checkpoints/SDXL1.0/` not flat `checkpoints/`. Moved file to flat `checkpoints/`. After both fixes, batch ran.
- **Reviewer:** cold-start sub-agent per §12.4.

| Biome | Seed | C1 | C2 | C3 | C4 | V | Notes |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| chaos | 101 | ⚠ | ⚠ | ⚠ | ❌ | 0 | Bulbous pod w/ tendrils + embedded ring/gem; soil patch; green not violet |
| chaos | 202 | ❌ | ❌ | ❌ | ❌ | 0 | Muscular humanoid creature with red headband, not a plant |
| chaos | 303 | ✅ | ⚠ | ✅ | ❌ | 1 | Spiky tendril mass, shrub-ish; white bg; blue-grey not violet |
| grassland | 101 | ✅ | ⚠ | ✅ | ✅ | 1 | Round leafy bulb w/ swirl flourishes; white bg; green ok |
| grassland | 202 | ❌ | ❌ | ❌ | ⚠ | 0 | Plant-camera hybrid object on blue platform |
| grassland | 303 | ⚠ | ❌ | ✅ | ✅ | 0 | Anthropomorphic plant-creature with eyes/legs |
| snow | 101 | ❌ | ❌ | ❌ | ✅ | 0 | Fairy/winged character with staff, not a plant |
| snow | 202 | ❌ | ❌ | ❌ | ⚠ | 0 | Anime girl character on gradient bg |
| snow | 303 | ❌ | ❌ | ⚠ | ✅ | 0 | Ice ghost/slime creature with camera, not a plant |

**Aggregate:** C1 2/9 · C2 0/9 · C3 3/9 · C4 3/9 · **Verdict: 0 clean / 2 partial / 7 fail.**

**Per-biome:** chaos 0/1/2 · grassland 0/1/2 · **snow 0/0/3 (100% character collapse)**

**Failure modes (sub-agent verbatim):**
> Model is rendering JRPG character/mascot art, not props. Img2: muscular plant-man. Img4: leafy bulb (shrub-ish). Img5: plant-camera chimera on platform. Img6: cabbage-creature with legs/eyes. Img7: ice fairy girl. Img8: anime swordmaiden. Img9: ice ghost with camera. Snow biome fully collapsed to characters. Base checkpoint heziUltimateJapaneseAndKorean steers toward character/mascot subjects.

**Reviewer note (sub-agent verbatim):**
> Only Img4 (grassland s101) is marginally usable as an iso bush prop — rounded leafy mass, clean white bg, green palette. All other 7 are unusable. Checkpoint choice is wrong for prop sprites.

**Speed:** 57.9s for 9 imgs = ~6.4 s/img.

### Lesson confirmed twice: character-trained bases are non-viable for prop generation

| Iter | Base | Outcome on snow biome |
|---|---|---|
| 3 | Illustrious-XL (+ char LoRA) | anime faces 0/9 across all biomes |
| 9 | hezi Japanese-Korean | fairy/swordmaiden/ice ghost 0/9 on snow specifically |

Name pattern heuristic: any SDXL base with "anime", "character", "japanese", "korean", "portrait", "girl", "waifu", or country-prefix in the name is character-trained and will collapse on a non-character prompt regardless of prompt strength or anti-character negatives.

**Recommendation:** REJECT iter 9. **Working baseline remains iter 6 (`terrain-realisticfantasy-v30` + iter 6 prompt + zero LoRA).** Future base-model probes should restrict to *content-focused* training (realistic-fantasy mix, dreamshaper, terrain-tuned, photorealistic-fantasy, etc.) — never character/portrait/anime bases.

Artifacts: `experiments/tmp_009/hezi-static-v1-bush-spike-pack.json`, `outputs/spike-hezi-v1/`, alias `hezi-ultimate-jp-kr-v10` in models.yaml kept as experimental record. Checkpoint file was moved from `models/checkpoints/SDXL1.0/` to `models/checkpoints/` to work around the service's `_basename()` flattening.
