# Terrain Review Pass Pack (Track-First)

Purpose: run a deterministic terrain-tile evaluation pass and record every output
in `terrain-model-tracking.json`.

## 1) Terrain Families In Scope

- `grass_dirt`
- `snow_ice`
- `desert_sand`
- `swamp_mud`
- `lava_volcanic`
- `water_coast`

## 2) Fixed Run Config

- Size: `1024x1024`
- Steps: `24`
- CFG: `6.5`
- Sampler: `dpmpp_2m`
- Scheduler: `karras`
- Seeds: `101, 202, 303, 404, 505, 606`
- Negative prompt:
  - `worst quality, low quality, blurry, deformed, extra limbs, text watermark, logo, jpeg artifacts`

## 3) First-Pass Candidate Bundle

- `noobai-xl-v1.1 + fantasy/FantasyMap-Heavy (0.8)`
- `noobai-xl-v1.1 + fantasy/Battlemap-v1 (0.75)`
- `noobai-xl-v1.1 + scifi/SDXL_ScienceFictionStyle_v1 (0.75)`
- `chineseMartialArts_v10 + wuxia/wuxia-people-test-v1 (0.7)`

## 4) Prompt Pack

Use one terrain prompt per family from:

- `docs/architecture/terrain-model-tracking.json` -> `promptPack`

## 5) Output Naming Convention

Use stable names so metadata and files are easy to map:

- `<candidate_id>__<terrain_family>__s<seed>.png`

Example:

- `noobai-xl-v1.1+FantasyMap-Heavy__grass_dirt__s101.png`

## 6) API Payload Template

Use `POST /v1/images/generations` with:

- `model`: checkpoint id registered in `config/models.yaml`
- `prompt`: terrain-family prompt text
- `size`, `steps`, `cfg`, `sampler`, `scheduler`, `seed`
- `loras`: list of `{name, weight}`

Example payload shape:

```json
{
  "model": "noobai-xl-v1.1",
  "prompt": "top-down terrain tile ...",
  "size": "1024x1024",
  "steps": 24,
  "cfg": 6.5,
  "sampler": "dpmpp_2m",
  "scheduler": "karras",
  "seed": 101,
  "response_format": "url",
  "loras": [
    { "name": "fantasy/FantasyMap-Heavy", "weight": 0.8 }
  ]
}
```

## 7) Minimal Completion Gate (First Session)

To avoid drift/lost tracking, first session is considered complete when:

1. At least one generated image exists for each of the six terrain families.
2. Each generated output is appended to `runs` in metadata JSON.
3. `scores` section is filled and auto-processed via tracker script.
4. Candidate status transitions are synced into `genre-model-catalog.md`.

## 8) Tracker Script Usage

After manual scoring fields are filled:

- `python scripts/terrain-review-tracker.py --file docs/architecture/terrain-model-tracking.json score`

When you add run records in batch:

- `python scripts/terrain-review-tracker.py --file docs/architecture/terrain-model-tracking.json validate`

## 9) Human-Friendly Result Export

To download generated URLs into a reviewable folder tree:

- `python scripts/terrain-export-results.py --file docs/architecture/terrain-model-tracking.json --out-dir outputs/terrain-review/pass-001 --api-key test-gen-key`

This produces:

- Per-candidate folders:
  - `outputs/terrain-review/pass-001/<candidate_id>/<terrain_family>/...png`
- Human-readable index:
  - `outputs/terrain-review/pass-001/index.md`

Review from local files first, then fill `scores` in metadata.

