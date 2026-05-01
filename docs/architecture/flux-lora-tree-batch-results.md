# Flux tree LoRA batch review (GGUF Q8)

Operator preference is captured here so future batches and presets stay aligned.

## Setup

| Field | Value |
|--------|--------|
| Model | `flux1-dev-q8-tree` |
| Workflow | `workflows/flux_gguf.json` |
| Prompt | Batch script uses **`tree-environment-batch-pack.json`** template (2.5D orthographic sprite + white ground); LoRA comparison row used a fixed oak prompt for A/B only. |
| Size | `1024x1024` |
| `transparent_background` | `true` |
| Seed | `404` |
| Steps | `28` |
| CFG | `1.0` |
| Sampler / scheduler | `euler` / `simple` |
| LoRA weight (all trials) | `0.8` |

Host LoRA root: `models/loras/` → API names like `flux/<basename>` (no `.safetensors` suffix).

## Batch outputs

Artifacts directory (same settings, one PNG per LoRA):

`outputs/tree-review/flux-lora-batch-test/`

Machine-readable metrics:

`outputs/tree-review/flux-lora-batch-test/metrics.csv`  
(from `uv run python scripts/image-quality-metrics.py "outputs/tree-review/flux-lora-batch-test/*.png"`)

## Results (no-reference metrics)

Interpret **relative** to this folder only; higher `edge_std` / `hf_ratio` usually mean more edge/detail energy.

| LoRA API name | PNG output | entropy | edge_std | hf_ratio | alpha_cov | size_kb |
|---------------|------------|---------|----------|----------|-----------|---------|
| `flux/chinese_wuxia_style` | `chinese_wuxia_style__w08__s404.png` | 5.071 | 34.916 | 0.0166 | 0.479 | 1083.1 |
| `flux/colorful_fantasy_art_flux` | `colorful_fantasy_art_flux__w08__s404.png` | 4.761 | 41.608 | 0.0228 | 0.448 | 1154.8 |
| **`flux/dark_fantasy_digital_v11`** | **`dark_fantasy_digital_v11__w08__s404.png`** | **4.932** | **47.579** | **0.0266** | **0.447** | **1349.1** |
| `flux/fantasy_art_dev_lora_729919` | `fantasy_art_dev_lora_729919__w08__s404.png` | 3.959 | 25.033 | 0.0076 | 0.408 | 726.9 |
| `flux/fantasy_flux_world` | `fantasy_flux_world__w08__s404.png` | 4.760 | 28.826 | 0.0105 | 0.451 | 871.8 |
| `flux/fantasy_world_2019995` | `fantasy_world_2019995__w08__s404.png` | 5.381 | 30.863 | 0.0137 | 0.467 | 1186.9 |
| `flux/wuxia_paseer` | `wuxia_paseer__w08__s404.png` | 5.124 | 27.639 | 0.0090 | 0.439 | 974.7 |
| `flux/xia3_711525` | `xia3_711525__w08__s404.png` | 5.365 | 42.637 | 0.0226 | 0.515 | 1290.5 |

## Decision

**Default Flux tree style LoRA:** `flux/dark_fantasy_digital_v11` at weight **`0.8`** (same batch).

- **On disk:** `models/loras/flux/dark_fantasy_digital_v11.safetensors`  
- **Reference render:** `outputs/tree-review/flux-lora-batch-test/dark_fantasy_digital_v11__w08__s404.png`  
- **Source download (archive):** `downloads/other-lora/669671_flux1_dev_Dark Fantasy Digital Art Style v1.1.safetensors` (copied + renamed for API-safe path)

Other LoRAs in this batch were strong alternatives (`flux/colorful_fantasy_art_flux`, `flux/xia3_711525`, etc.); revisit weights or prompts per biome/tree archetype as needed.

## Tree batch pack alignment

Production batch defaults live in **`tree-environment-batch-pack.json`**: same LoRA **`flux/dark_fantasy_digital_v11`** at **0.8**, Flux sampler **`euler` / `simple`**, **`cfg` 1.0**, **`steps` 28**, plus biome-specific **`tree_species`** so species match climate (glacier / infernal / desert / temperate / etc.).

## Kontext GGUF fallback (camera semantics)

If vanilla Flux Dev GGUF still ignores framing, copy **`downloads/flux1-kontext-dev-Q8_0.gguf`** → **`models/unet/flux1-kontext-dev-Q8_0.gguf`**, then add a registry sibling of **`flux1-dev-q8-tree`** that points `checkpoint:` at that filename and uses **`workflows/flux_gguf.json`**. Override with **`--model-override flux1-kontext-dev-q8-tree`** on the batch script after validating startup smoke.

Example registry stanza (merge into **`config/models.yaml`** once the file exists on disk):

```yaml
  - name: flux1-kontext-dev-q8-tree
    backend: comfyui
    family: flux
    workflow: workflows/flux_gguf.json
    checkpoint: unet/flux1-kontext-dev-Q8_0.gguf
    prediction: eps
    vae: vae/ae.safetensors
    clip_l: text_encoders/clip_l.safetensors
    t5xxl: text_encoders/t5xxl_fp8_e4m3fn.safetensors
    dual_clip_type: flux
    capabilities:
      image_gen: true
    defaults:
      size: "1024x1024"
      steps: 28
      cfg: 1.0
      sampler: euler
      scheduler: simple
      negative_prompt: "blurry, soft focus, low detail, watercolor wash, fog, haze, terrain tile, platform, pedestal, scene background, multiple objects, dark ground, colored ground, person, human, character, face"
    limits:
      steps_max: 60
      n_max: 2
      size_max_pixels: 1572864
    vram_estimate_gb: 10
```
