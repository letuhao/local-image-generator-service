# TMP_009 — Isometric Asset Pipeline spike (2026-05-23)

Off-production exploratory artifacts for [TMP_009 — Isometric Asset Pipeline](https://github.com/letuhao1994/lore-weave-zone-map-design) in the sister design repo. Everything here is **spike / experimental**. The validated implementation lives in the production paths:

| Production artifact | Purpose |
|---|---|
| `workflows/flux_gguf_nag.json` | Flux GGUF graph with the in-tree `NAGuidance` node (model patch) wired between `UnetLoaderGGUF` and `KSampler`. **`workflows/` is gitignored per repo convention** (user-managed local data), so this file is NOT tracked by git. The canonical reference copy lives at `experiments/tmp_009/flux_gguf_nag.workflow.json` (this directory). To make the `flux1-dev-q8-tree-nag` model alias work on a fresh clone, copy the workflow into place:<br>`cp experiments/tmp_009/flux_gguf_nag.workflow.json workflows/flux_gguf_nag.json` |
| `config/models.yaml` entry `flux1-dev-q8-tree-nag` | Model alias that routes Flux dev Q8 + `dark_fantasy_digital_v11` LoRA through the NAG workflow. |

The contents of this directory:

| File | What it is |
|---|---|
| `nag-cfg1.0-bush-spike-pack.json` | Pack JSON for the static-prop bush spike: 1 entry (`alpine_dwarf_shrub_cluster`) × 3 biomes (`abyss_chaos_rift`, `grassland_temperate`, `snow_frost`) × 3 seeds (101, 202, 303). CFG=1.0 + NAG + hardened anti-ground negative. Drives `scripts/homm3-biome-bundle-batch.py`. |
| `poc_static_consistency.py` | Post-process composite spike: tight-crop → optional bottom-trim → scale-normalise → paste onto a canonical 2:1 dimetric diamond. CLI args `--bundle / --out / --suffix`. **Background strip is the weak white-threshold step** — TMP_009 DEBT #8 will replace it with RMBG. |
| `poc_strict_prompt_compare.py` | 2-row A/B compare of original `homm3-bundle` vs the NAG strict pack for a single biome. |
| `poc_nag_biome_grid.py` | 3-biome × 3-seed grid of the strict-pack outputs — debt #4 generalisation honesty check. |
| `rmbg_cutout.py` | BRIA RMBG-1.4 cutout via the local ONNX export — debt #8 replacement for the white-threshold strip. ~1 s/image on CPU. Used by default in `poc_static_consistency.py`; pass `--no-rmbg` to fall back to the legacy threshold loop. |
| `flux_gguf_nag.workflow.json` | Reference copy of the production workflow (lives at `workflows/flux_gguf_nag.json` on a working install; gitignored per repo convention). Mirrors `workflows/flux_gguf.json` with a single `NAGuidance` node spliced between `UnetLoaderGGUF[1]` and `KSampler[6]`. See "Production artifact" table above for the install step. |

## Mutation log

The spike pack went through three revisions in one session. Each revision is
preserved in git history; this log captures what was learned at each step (so
the iteration cost isn't lost when the design doc was rewritten to match the
final state).

### v1 — CFG=1.0 + hardened anti-ground negative
- Same Flux model + LoRA + sampler as production bush pack
- Negative prompt expanded with `ground / rocks / platform / pedestal / base / terrain / soil / cast-shadow` terms
- **Result:** negligible improvement. Ground/base still baked into every output.
- **Why:** Flux dev distilled at CFG=1.0 triggers ComfyUI's `cfg1_optimization` which skips the uncond pass — the negative prompt is silently ignored.

### v2 — CFG=3.5 + aggressive positive rewrite
- Bumped CFG to make the negative bite
- Positive rewritten to demand "isolated cutout sprite, lowest leaves are the lowest opaque pixels, bottom 30% pure white"
- **Result:** ground GONE, **but LoRA style also gone** — output became generic pen-and-ink silhouettes
- **Why:** the user later confirmed the model only supports CFG=1.0; CFG=3.5 over-pushed and broke style. The aggressive positive also overrode the `dark_fantasy` LoRA.

### v3 — CFG=1.0 + NAG + original positive
- Rolled CFG back to 1.0
- Added in-tree `NAGuidance` node between `UnetLoaderGGUF` and `KSampler` via a new model alias `flux1-dev-q8-tree-nag`
- Positive restored to the original production bush template; hardened negative kept
- **Result on `chaos_rift`:** 3/3 seeds clean, style preserved.
- **Result on `grassland_temperate` (debt #4):** 1/3 clean, 1/3 partial, 1/3 iso-platform baked.
- **Result on `snow_frost` (debt #4):** 0/3 clean, 1 subject drift to snow-covered rock pile, 2 base baked.
- **Why partial:** NAG enforces the negative correctly (`disable_model_cfg1_optimization()` + L1-normalised guided attention), but the model's biome-prior pulls toward ground depiction more strongly on light/cold palettes. Default `nag_scale=5.0` dampens but doesn't eliminate.

The current `nag-cfg1.0-bush-spike-pack.json` IS the v3 state.

## How to re-run

ComfyUI + image-gen-service must be up (`docker compose up -d` from repo root,
default backend is `comfyui-clean`). Then:

```bash
# 1. Regen (NAG path):
API_KEY=$(grep '^API_KEYS=' .env | sed -E 's/^API_KEYS=//; s/#.*//; s/[[:space:]]+$//' | cut -d',' -f1 | xargs)
.venv/Scripts/python.exe scripts/homm3-biome-bundle-batch.py \
  --pack experiments/tmp_009/nag-cfg1.0-bush-spike-pack.json \
  --base-url http://127.0.0.1:8700 --api-key "$API_KEY" \
  --out-dir outputs/spike-strict-prompt --resume

# 2. Biome generalisation grid (debt #4):
.venv/Scripts/python.exe experiments/tmp_009/poc_nag_biome_grid.py

# 3. End-to-end composite on one biome:
.venv/Scripts/python.exe experiments/tmp_009/poc_static_consistency.py \
  --bundle outputs/spike-strict-prompt/abyss_chaos_rift/bush \
  --out outputs/spike-static-consistency-nag --suffix _nag

# 4. A/B compare (chaos_rift only — needs both NAG outputs and the original homm3-bundle):
.venv/Scripts/python.exe experiments/tmp_009/poc_strict_prompt_compare.py
```

## What this spike does NOT yet validate

See TMP_009 §11 AC + the DEBT items in the design repo's SESSION_HANDOFF:

- Cross-entry generalisation (n=9 = one entry × three biomes × three seeds — not multiple entries)
- RMBG model swap (DEBT #8 — load-bearing because white-threshold cannot trim coloured baked bases)
- Per-biome NAG/negative tuning or two-pass retry strategy
- Actor multi-facing (TMP-ASSET-Q10, separate spike required)
- Atlas packing + Phaser consumption (stage 4 of the §5 pipeline)
