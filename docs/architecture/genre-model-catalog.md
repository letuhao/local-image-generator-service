# Genre Model Catalog And Gap Analysis

> Status: draft v0.1  
> Scope: planning-only inventory, mapping, and download queue

---

## 1. Current Local Inventory

### 1.1 Runtime-Registered Models (`config/models.yaml`)

| Name | Backend | Checkpoint | Notes |
|---|---|---|---|
| `noobai-xl-v1.1` | comfyui | `models/checkpoints/NoobAI-XL-v1.1.safetensors` | Current stable SDXL-style baseline |
| `chroma-hd-q8` | comfyui | `models/unet/chroma1-hd-q8.gguf` | Flux/chroma pipeline with `clip_l` + `t5xxl` |

### 1.2 Additional Local Checkpoints (not yet registered)

- `absolutereality_v181.safetensors`
- `dreamshaper_8.safetensors`
- `fantasy map-heavy.safetensors`
- `flux1-dev.safetensors`
- `meinamix_v12Final.safetensors`
- `sd_xl_base_1.0.safetensors`

### 1.3 Encoders / VAE

- Text encoders:
  - `clip_l.safetensors`
  - `t5xxl_fp8_e4m3fn.safetensors`
- VAE:
  - `ae.safetensors`
  - `sdxl_vae.safetensors`
  - `sdxl.vae.safetensors`
  - `clearvaeSD15_v23.safetensors`
  - `vae-ft-mse-840000-ema-pruned.safetensors`
  - `vaeKlF8Anime2_klF8Anime2VAE.safetensors`

### 1.4 LoRA Directories (counts)

- `hanfu`: 66 files
- `mics`: 95 files
- `mature_theme_group`: 82 files
- `mature_theme_expression`: 43 files

> Note: mature-theme labels are intentionally sanitized in this document for readability.

---

## 2. Genre Mapping (Current Usefulness)

Legend:

- `ready`: stack likely usable now
- `partial`: usable with constraints
- `missing`: no strong local stack yet

| Genre | Current Status | Primary Candidate Stack | Notes |
|---|---|---|---|
| wuxia | partial | `noobai-xl-v1.1` + curated `hanfu` LoRAs | Good style hints exist in `hanfu`, needs curation list |
| fantasy | partial | `fantasy map-heavy` or `dreamshaper_8` + `mics` | Likely workable for props/POI, needs quality check |
| sci-fi | missing | fallback to `noobai-xl-v1.1` | No clear sci-fi-focused checkpoint/LoRA set identified locally |
| horror | missing | fallback to `noobai-xl-v1.1` | No explicit horror stack identified locally |
| historical | partial | `sd_xl_base_1.0` + curated `hanfu`/`mics` | Needs style-specific LoRA shortlist |
| modern | partial | `absolutereality_v181` + `mics` | Potentially strong for modern realism props; unverified |
| general fallback | ready | `noobai-xl-v1.1` | Existing registered fallback path |

---

## 3. Candidate Stack Policy By Genre

Per genre stack should include:

- `primary_base_model`
- `backup_base_model`
- `approved_lora_shortlist` (2-5 entries for consistency)
- `default_prompt_profile_id`

Current blocker:

- LoRA folders are broad collections; no approved shortlists yet per genre.

---

## 4. Gaps Requiring Downloads

### 4.1 Priority 1 (coverage gaps)

- sci-fi dedicated base model (checkpoint)
- horror dedicated base model (checkpoint)

### 4.2 Priority 2 (consistency gaps)

- genre-focused LoRA packs for:
  - fantasy environment props
  - modern urban props
  - historical architecture/props beyond hanfu style

### 4.3 Priority 3 (optimization/quality expansion)

- alternate flux/sdxl-compatible checkpoints for stylistic diversity
- cleaner VAE choices per base-model family to reduce artifacts

---

## 5. Download Queue Template (Actionable)

Use this queue format for each requested model:

| Priority | Genre | Model Type | Candidate Name | Source | Target Folder | Reason |
|---|---|---|---|---|---|---|
| P1 | sci-fi | checkpoint | `TBD` | `TBD` | `models/checkpoints/` | fill missing dedicated stack |
| P1 | horror | checkpoint | `TBD` | `TBD` | `models/checkpoints/` | fill missing dedicated stack |
| P2 | fantasy | lora | `TBD` | `TBD` | `models/loras/mics/` | improve style consistency |
| P2 | modern | lora | `TBD` | `TBD` | `models/loras/mics/` | improve prop realism consistency |

`TBD` values should be filled only after manual model selection review.

---

## 6. Acceptance Checklist For New Downloads

Before a downloaded model is moved from candidate to approved:

- File integrity verified (checksum recorded).
- Correct destination folder and naming convention applied.
- Compatible workflow path identified.
- 6-12 sample generations reviewed for target genre/object classes.
- VRAM and latency are acceptable on current host.
- Catalog row updated from `candidate` to `approved`.

---

## 7. Recommended Next Discussion

Decide the first concrete shortlist:

1. Pick 1 sci-fi checkpoint and 1 horror checkpoint candidate.
2. Pick 3 LoRAs each for wuxia/fantasy/modern.
3. Define first approved prompt profiles per genre.

After that, we can create a concrete download/move/registration execution plan.

---

## 8. Execution Plan v1 (Approved Scope)

Locked decisions:

- Priority genres: `fantasy`, `wuxia`, `sci-fi`
- Mature-theme policy: `optional profile only`
- Download sources: `Civitai` + `HuggingFace`
- Quality gate: `manual + simple checklist scoring`
- Registration policy: `download + catalog first` (no runtime registration yet)

### 8.1 Folder Target Rules

- Checkpoints: `models/checkpoints/`
- VAE: `models/vae/`
- Text encoders: `models/text_encoders/`
- LoRAs (default profiles): `models/loras/<genre>/`
- LoRAs (optional mature profile): `models/loras/optional_mature/`

### 8.2 Genre Shortlist Table (v1)

#### Fantasy

| Role | Candidate | Type | Current State | Source | Target Folder | Notes |
|---|---|---|---|---|---|---|
| Primary | `fantasy map-heavy.safetensors` | checkpoint | local | local | `models/checkpoints/` | strong map/prop style signal |
| Backup | `dreamshaper_8.safetensors` | checkpoint | local | local | `models/checkpoints/` | broad fantasy baseline |
| LoRA-1 | `Fantasy Map - Heavy` | lora | downloaded | Civitai | `models/loras/fantasy/` | https://civitai.com/models/382959/fantasy-map |
| LoRA-2 | `Battlemap v1.0` | lora | downloaded | Civitai | `models/loras/fantasy/` | https://civitai.com/models/613017/battlemap |
| LoRA-3 | `Fantasy Style v1.0` | lora | downloaded | Civitai | `models/loras/fantasy/` | downloaded local candidate: `FantasyStyleWAI_Maly007.safetensors` |
| Optional Mature | `TBD_fantasy_optional_mature_1` | lora | missing | civitai/hf | `models/loras/optional_mature/` | optional profile only |

#### Wuxia

| Role | Candidate | Type | Current State | Source | Target Folder | Notes |
|---|---|---|---|---|---|---|
| Primary | `noobai-xl-v1.1` | checkpoint | registered | local | `models/checkpoints/` | stable baseline for prompt control |
| Backup | `sd_xl_base_1.0.safetensors` | checkpoint | local | local | `models/checkpoints/` | neutral SDXL fallback |
| LoRA-1 | `SDXL Chinese Ancient Architecture v1.0` | lora | candidate_pending_review | Civitai | `models/loras/wuxia/` | https://civitai.com/models/269281/sdxl-3d-rendering-style-chinese-ancient-architecture |
| LoRA-2 | `wuxia people / 武侠人物 test v1.0` | lora | downloaded | Civitai | `models/loras/wuxia/` | https://civitai.com/models/251768/wuxia-people-test |
| LoRA-3 | `hanfu_curated_prop_C` | lora | local candidate | local | `models/loras/wuxia/` | curate from existing `models/loras/hanfu` set |
| Optional Mature | `TBD_wuxia_optional_mature_1` | lora | missing | civitai/hf | `models/loras/optional_mature/` | optional profile only |

#### Sci-Fi

| Role | Candidate | Type | Current State | Source | Target Folder | Notes |
|---|---|---|---|---|---|---|
| Primary | `LeThAiVN Science Fiction SDXL v1.0` | checkpoint | downloaded | Civitai | `models/checkpoints/` | https://civitai.com/models/533703/lethaivnscience-fictionsdxl |
| Backup | `flux1-dev.safetensors` | checkpoint | local | local | `models/checkpoints/` | experimental backup candidate |
| LoRA-1 | `SDXL Science Fiction Style v1.0` | lora | downloaded | Civitai | `models/loras/scifi/` | https://civitai.com/models/122837/sdxl-science-fiction-style |
| LoRA-2 | `sdxl-70s-scifi` | lora | downloaded | HuggingFace | `models/loras/scifi/` | https://huggingface.co/jakedahn/sdxl-70s-scifi |
| LoRA-3 | `TBD_scifi_prop_pack_C` | lora | missing | civitai/hf | `models/loras/scifi/` | keep one open slot after first review pass |
| Optional Mature | `TBD_scifi_optional_mature_1` | lora | missing | civitai/hf | `models/loras/optional_mature/` | optional profile only |

### 8.3 Prioritized Download Queue (Catalog Stage)

| Priority | Genre | Model Type | Candidate Name | Source | Target Folder | Reason | Status |
|---|---|---|---|---|---|---|---|
| P1 | sci-fi | checkpoint | `LeThAiVN Science Fiction SDXL v1.0` | https://civitai.com/models/533703/lethaivnscience-fictionsdxl | `models/checkpoints/` | fill missing primary genre base | downloaded |
| P1 | sci-fi | lora | `SDXL Science Fiction Style v1.0` | https://civitai.com/models/122837/sdxl-science-fiction-style | `models/loras/scifi/` | minimum viable style control | downloaded |
| P1 | sci-fi | lora | `sdxl-70s-scifi` | https://huggingface.co/jakedahn/sdxl-70s-scifi | `models/loras/scifi/` | alternate sci-fi style lane | downloaded |
| P2 | fantasy | lora | `Fantasy Map - Heavy` | https://civitai.com/models/382959/fantasy-map | `models/loras/fantasy/` | tighten fantasy prop consistency | downloaded |
| P2 | fantasy | lora | `Battlemap v1.0` | https://civitai.com/models/613017/battlemap | `models/loras/fantasy/` | tighten fantasy POI consistency | downloaded |
| P2 | wuxia | lora | `wuxia people / 武侠人物 test v1.0` | https://civitai.com/models/251768/wuxia-people-test | `models/loras/wuxia/` | promote dedicated wuxia identity | downloaded |
| P3 | wuxia | checkpoint | `chineseMartialArts_v10` | local(downloads) | `models/checkpoints/` | add diversity backup | downloaded |
| P3 | fantasy | checkpoint | `TBD_fantasy_alt_base` | civitai/hf | `models/checkpoints/` | add diversity backup | planned |

### 8.4 Manual Quality Checklist Scoring (Simple)

Per candidate model/stack, score 0-2 each:

- Genre fit
- Object readability (small scale)
- Style consistency across 6-12 samples
- Artifact severity
- Prompt responsiveness

Decision thresholds:

- `8-10`: approved candidate
- `6-7`: keep for tuning
- `<6`: reject

### 8.5 Catalog-Only Promotion Flow

For each planned candidate, status flow is:

`planned -> downloaded -> reviewed -> approved_candidate`

No move to runtime registration in this phase.

### 8.6 Candidate Verification Notes

- All external candidates above are discovery-level references and must pass manual quality scoring before approval.
- Confirm each candidate's license and usage terms before production use.
- Keep mature-theme routing restricted to optional profiles only.
- Local files moved from `downloads` and currently `candidate_pending_review`:
  - Fantasy: `NewFantasyCoreV4_ILL_by_VisionaryAI_.safetensors`, `zimage_fantasy_v1.safetensors`
  - Wuxia: `wuxia2.safetensors`, `Wuxie7.safetensors`, `wuxia_female_1-jade-000009.safetensors`

### 8.7 Terrain-First Review Tracking (Current Pass)

- Source of truth file: `docs/architecture/terrain-model-tracking.json`
- Prompt/run protocol: `docs/architecture/terrain-review-pass-pack.md`
- Tracker tool: `scripts/terrain-review-tracker.py`
- First terrain sweep status:
  - 6/6 terrain families generated (`grass_dirt`, `snow_ice`, `desert_sand`, `swamp_mud`, `lava_volcanic`, `water_coast`)
  - Baseline candidate `noobai-xl-v1.1+baseline-no-lora`: `reviewed` (initial total `6/10`)
  - `terrain-sdxl-base + fantasy/FantasyMap-Heavy (0.8)`: generates readable world-map compositions, but not seamless terrain texture tiles (track as map-layout style, not tile-texture candidate)
  - `terrain-sdxl-base + texture/575474_SDXL_Textures (0.55)`: latest pass is usable for close-up terrain texture generation when using material-style prompting (not map-layout prompting)
  - `terrain-sdxl-base + texture/53858_SDXL_sxz-texture-sdxl (0.55)`: current best terrain-texture candidate in the active SDXL lane
  - `terrain-sdxl-base + terrain/StylizedTexture_ZIT`: rejected for current SDXL lane (incompatible output pattern in this runtime)
  - Extended pack run completed: `outputs/terrain-53858-pack-v1/` (6 terrain families x 3 seeds, 18 outputs total)
  - LoRA stack candidates remain `candidate_pending_review` until compatible run pass is completed

### 8.8 Working Texture Preset (Current Best)

- Goal: seamless-ish terrain surface texture (close-up material view), not world-map composition.
- Recommended runtime combo:
  - Model: `terrain-sdxl-base`
  - LoRA: `texture/53858_SDXL_sxz-texture-sdxl`
  - LoRA weight: `0.55`
  - Size: `1024x1024` (downscale after generation if needed for tile packing)
  - Steps: `28`
  - CFG: `5.5`
- Prompt framing rule:
  - Describe a close-up ground material filling the full frame (as if top-down photo/material sample), not a map scene.
- Reusable prompt baseline:
  - Positive: `seamless top-down texture, wet dark swamp mud, cracked dried mud surface, scattered moss, small puddles with murky reflection, dead leaves, hand-painted strategy game art style, organic surface, fills entire frame, close-up ground view, muted green-brown palette, high detail surface`
  - Negative: `grid lines, tile borders, cell pattern, map view, zoomed out, multiple tiles, tiled arrangement, person, human, character, face, creature, monster, blurry, soft, washed out, 3D render, photorealistic`

### 8.9 Tile Candidate Decision Snapshot

| Candidate | Current Decision | Why |
|---|---|---|
| `terrain/StylizedTexture_ZIT` | rejected_incompatible | Expected ZImageTurbo lane; produced non-usable pattern output in current SDXL lane |
| `fantasy/FantasyMap-Heavy` | map_layout_only | Good for macro world-map composition, weak for close-up seamless texture tiles |
| `texture/575474_SDXL_Textures` | reviewed_keep | Usable with close-up material framing |
| `texture/53858_SDXL_sxz-texture-sdxl` | approved_candidate | Best quality/consistency in latest terrain pack pass |

### 8.10 Next Implementation Phase

- Adopt preset terminology consistently:
  - `asset_type`
  - `preset_id`
  - `variables`
  - `overrides`
- Promote current best tile preset to registry candidate (`terrain-53858-v1`) using:
  - `docs/architecture/preset-contract.md`
- Keep tile architecture and pass protocol aligned with:
  - `docs/architecture/tile-generation-architecture.md`
