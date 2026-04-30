# Preset Contract (Asset Generation)

> Status: draft v0.1  
> Scope: documentation-level contract for reusable generation presets

---

## 1. Purpose

Provide a stable preset contract so users and LLM agents can:

- reuse proven generation recipes
- override only selected fields safely
- preserve traceability of the final effective config

---

## 2. Contract Shape

## 2.1 Preset Identity

- `id`: stable preset id (example: `terrain-53858-v1`)
- `version`: semantic version string
- `asset_type`: example values `tile_texture`, `map_layout`
- `status`: `draft` | `approved` | `deprecated`
- `confirmed`: boolean marker for production-verified model+combo presets
- `description`: concise intended use

## 2.2 Bundle Defaults

- `model`: default model id
- `loras[]`:
  - `name`
  - `weight`
- optional `bundle_notes`

## 2.3 Prompt Template

- `positive_template`: prompt text with variables
- `negative_template`: default negative prompt
- `variables_schema`:
  - variable name
  - allowed values or pattern
  - required flag

## 2.4 Generation Defaults

- `size`
- `steps`
- `cfg`
- `seed_policy` (fixed seed or caller-provided seed)
- optional sampler/scheduler defaults

## 2.5 Override Policy

- `allowed_override_fields[]`
- per-field min/max ranges where relevant
- optional denylist fields (cannot be overridden)

## 2.6 Emitted Metadata (per run)

- preset id + version
- resolved model + loras
- resolved prompt + negative prompt
- final runtime params
- output path/url

---

## 2.7 Runtime Lifecycle Field

Preset-driven requests should include:

- `runtime_mode`: `manual` | `auto`

Semantics:

- `manual`: request assumes bundle is already active; service should fail fast if not.
- `auto`: service may perform internal bundle activation/switch before generation.

This field controls orchestration behavior only; it does not change the preset's
stylistic defaults.

---

## 3. Example Preset: `terrain-53858-v1`

```yaml
id: terrain-53858-v1
version: 1.0.0
asset_type: tile_texture
status: approved
description: Close-up terrain texture generation for strategy-map tile packs.

bundle:
  model: terrain-sdxl-base
  loras:
    - name: texture/53858_SDXL_sxz-texture-sdxl
      weight: 0.55

prompt_template:
  positive_template: >
    seamless top-down texture, {terrain_material},
    hand-painted strategy game art style, organic surface,
    fills entire frame, close-up ground view,
    muted {palette} palette, high detail surface
  negative_template: >
    grid lines, tile borders, cell pattern, map view,
    zoomed out, multiple tiles, tiled arrangement,
    person, human, character, face, creature, monster,
    blurry, soft, washed out, 3D render, photorealistic
  variables_schema:
    - name: terrain_material
      required: true
      example: wet dark swamp mud, cracked dried mud surface, scattered moss
    - name: palette
      required: true
      example: green-brown

generation_defaults:
  size: 1024x1024
  steps: 28
  cfg: 5.5
  seed_policy: caller_or_default
  default_seed: 101
  runtime_mode: auto

overrides:
  allowed_override_fields:
    - seed
    - cfg
    - steps
    - size
    - loras[0].weight
    - prompt_template.variables.terrain_material
    - prompt_template.variables.palette
  ranges:
    cfg: [4.5, 7.0]
    steps: [20, 36]
    loras[0].weight: [0.45, 0.65]
```

---

## 4. Preset Lifecycle

1. `draft`: initial candidate preset
2. `approved`: validated by multi-seed multi-family pass
3. `deprecated`: no longer recommended, retained for compatibility/audit

---

## 5. Next Implementation Phase

Implementation follow-up should add:

1. Preset registry files under `config/presets/`
2. Preset schema validation on load
3. `GET /v1/presets` + `GET /v1/presets/{id}`
4. Preset resolve path in generation facade (`/v1/assets/generate`)
