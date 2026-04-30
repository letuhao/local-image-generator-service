# Genre Review Pass Pack (One Session)

Purpose: run a fast, consistent manual review for downloaded fantasy/wuxia/sci-fi candidates and decide `approved_candidate` vs `reject`.

Status flow reminder: `downloaded -> reviewed -> approved_candidate` (or reject)

## 1) Session Scope

Review these candidates first (current highest priority):

- Sci-fi checkpoint: `LeThAiVN_ScienceFiction_SDXL_v1.safetensors`
- Sci-fi LoRAs: `SDXL_ScienceFictionStyle_v1.safetensors`, `sdxl-70s-scifi.safetensors`
- Fantasy LoRAs: `FantasyMap-Heavy.safetensors`, `Battlemap-v1.safetensors`, `FantasyStyleWAI_Maly007.safetensors`
- Wuxia checkpoint alt: `chineseMartialArts_v10.safetensors`
- Wuxia LoRAs: `wuxia-people-test-v1.safetensors`, `wuxia2.safetensors`, `Wuxie7.safetensors`

Optional (time permitting):

- Fantasy LoRAs: `NewFantasyCoreV4_ILL_by_VisionaryAI_.safetensors`, `zimage_fantasy_v1.safetensors`
- Wuxia LoRA: `wuxia_female_1-jade-000009.safetensors`

## 2) Fixed Generation Settings

Use these exact settings for every candidate unless a model hard-fails:

- Size: `1024x1024`
- Steps: `24`
- CFG: `6.5`
- Sampler: `dpmpp_2m`
- Scheduler: `karras`
- Seed set: `101, 202, 303, 404, 505, 606`
- Outputs per prompt: `1`
- Negative prompt:
  - `worst quality, low quality, blurry, deformed, extra limbs, text watermark, logo, jpeg artifacts`

If a candidate repeatedly fails at `1024x1024`, retry once at `832x1216` and record the fallback.

## 3) Prompt Pack

Run all 6 prompts per candidate with the seed set above.

### 3.1 Fantasy Prompts

1. `isometric fantasy town center with market stalls, fountain, stone roads, bright daytime, highly readable game asset composition`
2. `fantasy forest shrine with moss ruins, pathway props, lanterns, top-down strategy map style`
3. `battlefield outpost with wooden palisade, tents, supply crates, clear object silhouettes, fantasy style`
4. `coastal fantasy harbor with docks, ships, cargo props, map-ready environmental readability`
5. `mountain pass checkpoint with watchtower, bridge, and props, fantasy strategy asset mood`
6. `arcane workshop exterior with magical machinery props, clean layout, consistent fantasy look`

### 3.2 Wuxia Prompts

1. `ancient martial arts courtyard with training props, banners, stone lanterns, wuxia style, readable environment assets`
2. `jianghu riverside inn exterior with docks, boats, signboards, wuxia atmosphere, clean composition`
3. `misty mountain sect gate with stairs, guardian statues, and architectural props, wuxia visual language`
4. `night market street in ancient city, paper lanterns, stalls, wuxia period details, strategy-map readability`
5. `bamboo forest duel ground with ritual props and pathways, cinematic wuxia tone`
6. `cliffside temple approach with bridges and carved stone details, wuxia environment assets`

### 3.3 Sci-Fi Prompts

1. `orbital station docking bay with modular props, crates, consoles, hard-surface sci-fi style, readable layout`
2. `neon cyberpunk alley with vents, cables, kiosks, and signage shapes, clean sci-fi asset readability`
3. `desert research outpost with solar arrays, antenna towers, and utility structures, sci-fi strategy map style`
4. `interior control room with panels, holographic terminals, and machinery props, consistent sci-fi style`
5. `ice planet mining camp with heavy equipment, pipelines, and transport pads, clear object silhouettes`
6. `abandoned spacecraft hangar with debris, catwalks, and maintenance props, cinematic sci-fi mood`

## 4) Scoring Rules (0-2 each)

Score each candidate across all generated outputs:

- Genre fit (0-2)
- Object readability at gameplay scale (0-2)
- Style consistency across seeds/prompts (0-2)
- Artifact severity (0-2, where 2 means few artifacts)
- Prompt responsiveness (0-2)

Total score:

- `8-10`: mark `approved_candidate`
- `6-7`: keep as `reviewed` (needs tuning)
- `<6`: mark `rejected`

Hard fail override (auto reject):

- Frequent anatomy/structure collapse
- Unusable composition in most prompts
- Severe repeated artifacts

## 5) One-Session Run Order

1. Sci-fi pass (checkpoint + 2 LoRAs)
2. Fantasy pass (3 core LoRAs)
3. Wuxia pass (alt checkpoint + 3 core LoRAs)
4. Optional overflow candidates if time remains
5. Fill scoring CSV
6. Update `docs/architecture/genre-model-catalog.md` statuses and notes

## 6) Output Naming Convention

Use a consistent output prefix:

- `<genre>_<candidate>_<promptIdx>_<seed>`

Example:

- `fantasy_FantasyMap-Heavy_p03_s404.png`

## 7) Fast Decision Checklist

For each candidate, confirm:

- Is this better than current same-genre backup?
- Does it keep recognizable props/POI structure?
- Is style stable enough for production prompts?
- Any license concern noted?

If 3 or more answers are "no", do not approve.

## 8) CSV Auto-Scoring Helper

After you fill the 5 score columns in `docs/architecture/genre-review-scoring-template.csv`,
run:

- `python scripts/score-genre-review.py`

Optional custom path:

- `python scripts/score-genre-review.py --csv docs/architecture/genre-review-scoring-template.csv`

This script auto-fills:

- `total_score_0_10` (sum of five 0-2 fields)
- `decision` (`approved_candidate` / `reviewed` / `rejected`)
