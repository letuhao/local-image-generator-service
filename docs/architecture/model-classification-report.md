# Model Classification Report

Snapshot generated: 2026-04-30

Scope: `models/checkpoints` and curated LoRA folders (`sd15`, `sdxl`, `flux`, `flux2`, `pony-xl`).

Detection method:
- Read safetensors `__metadata__` header.
- Primary keys: `modelspec.architecture`, `ss_base_model_version`.
- Folder fallback used when metadata is missing.

## Family Summary

- `sd15`: 13 files
- `sdxl`: 24 files
- `flux`: 10 files
- `flux2`: 1 file
- `pony-xl`: 1 file
- Total in this snapshot: 49 files

## File Snapshot

| File | Detected family | Evidence |
|---|---|---|
| `models/checkpoints/absolutereality_v181.safetensors` | `sd15` | size/profile fallback |
| `models/checkpoints/artUniverse_v80SDXL.safetensors` | `sdxl` | size/profile fallback |
| `models/checkpoints/chineseMartialArts_v10.safetensors` | `sd15` | size/profile fallback |
| `models/checkpoints/dreamshaper_8.safetensors` | `sd15` | size/profile fallback |
| `models/checkpoints/fantasticCharacters_v55.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base` |
| `models/checkpoints/fantasy map-heavy.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/checkpoints/flux1-dev.safetensors` | `flux` | `modelspec.architecture=Flux.1-dev` |
| `models/checkpoints/LeThAiVN_ScienceFiction_SDXL_v1.safetensors` | `sdxl` | size/profile fallback |
| `models/checkpoints/meinamix_v12Final.safetensors` | `sd15` | size/profile fallback |
| `models/checkpoints/NoobAI-XL-v1.1.safetensors` | `sdxl` | size/profile fallback |
| `models/checkpoints/realisticFantasyMix_v30.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base` |
| `models/checkpoints/SD-v1-5-pruned-emaonly.safetensors` | `sd15` | size/profile fallback |
| `models/checkpoints/sd_xl_base_1.0.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base` |
| `models/loras/sd15/109044_SD1.5_GameIconResearch_PLANT2_Lora.safetensors` | `sd15` | folder + name |
| `models/loras/sd15/116525_SD1.5_chibi_3in1_v1.safetensors` | `sd15` | folder + name |
| `models/loras/sd15/245096_SD1.5_Box series - European and American monomer concept scene.safetensors` | `sd15` | `ss_base_model_version=sd_v1` |
| `models/loras/sd15/447166_SD1.5_HEZI game icon equipment design.safetensors` | `sd15` | `modelspec.architecture=stable-diffusion-v1/lora` |
| `models/loras/sd15/500663_SD1.5_HEZI vertical version pixel game style.safetensors` | `sd15` | `modelspec.architecture=stable-diffusion-v1/lora` |
| `models/loras/sd15/508627_SD1.5_HEZI game concept scenegame wallpaper.safetensors` | `sd15` | `modelspec.architecture=stable-diffusion-v1/lora` |
| `models/loras/sd15/94022_SD1.5_GameIconResearch_build_Lora.safetensors` | `sd15` | folder + name |
| `models/loras/sdxl/Bai_LingMiao.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/sdxl/checkpoint-e18_s306.safetensors` | `sdxl` | `ss_base_model_version=sdxl_a1111_compat` |
| `models/loras/sdxl/df_style_v1.1.safetensors` | `sdxl` | folder fallback (metadata ambiguous) |
| `models/loras/sdxl/EVN5VJPFQPNNDSX862W3JRBTQ0.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl/lora` |
| `models/loras/sdxl/fantasyAI-000008.safetensors` | `sdxl` | folder fallback |
| `models/loras/sdxl/granblue_fantasy_v2.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/sdxl/qwyu_lin_v3.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/flux/1751271282.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/1751273732.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/1751878057.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/1753931311.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/1753931961.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/gokaygokayFlux-2D-Game-Assets-LoRA.safetensors` | `flux` | folder + name |
| `models/loras/flux/gokaygokayFlux-Game-Assets-LoRA-v2.safetensors` | `flux` | folder + name |
| `models/loras/flux/Granblue_Fantasy_Illustration_Style_for_FLUX.safetensors` | `flux` | `modelspec.architecture=flux-1-dev/lora` |
| `models/loras/flux/ral-dark-fantasy-flux.safetensors` | `flux` | folder + name (metadata inconsistent) |
| `models/loras/flux2/RE9FS691KJ73ZP21SY4VY0QT40.safetensors` | `flux2` | `modelspec.architecture=flux-2/lora` |
| `models/loras/pony-xl/Fant5yP0ny.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/ff12fran-pdxl-nvwls-v1.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/ffscarlet-illu-nvwls-v2.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/ffscarlet-pdxl-nvwls-v1.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/narmaya-illu-nvwls-v1.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/narmaya-pdxl-nvwls-v1.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |
| `models/loras/pony-xl/wuxia-000005.safetensors` | `sd15` | `modelspec.architecture=stable-diffusion-v1/lora` |
| `models/loras/pony-xl/Wuxia-PONY-PAseer.safetensors` | `pony-xl` | folder + name |
| `models/loras/pony-xl/Wuxia_Style_PONY_XL_by_UOC.safetensors` | `sdxl` | `modelspec.architecture=stable-diffusion-xl-v1-base/lora` |

## Manual Review Notes

- `models/checkpoints/fantasy map-heavy.safetensors` appears LoRA-like by metadata (`.../lora`) but is currently in checkpoints.
- `models/loras/sdxl/df_style_v1.1.safetensors` has conflicting hints (folder says SDXL; metadata includes `sd_1.5`).
- `models/loras/flux/ral-dark-fantasy-flux.safetensors` has flux naming but metadata hint includes `sd_1.5`; validate in ComfyUI before production use.
- `models/loras/pony-xl` mostly resolves to SDXL-family LoRAs (Pony/PDXL ecosystem).
