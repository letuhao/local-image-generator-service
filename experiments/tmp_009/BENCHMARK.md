# TMP_009 Asset-Pipeline Benchmark — Working Baseline & Iteration Tally

Single-page reference for the multi-model asset-pipeline spike. Detailed per-
iteration write-ups + sub-agent tables live in [`REVIEW.md`](REVIEW.md). This
file is the dashboard.

Last updated: **2026-05-23** after iteration 16. **Base-model probe phase closed** after iter 11. **Language-only intervention CEILING confirmed** at iter 13. **Structural intervention validated** at iter 14-16: iter 15 (canny + arc hint) = **STRONGEST variant** so far (1 clean + 5 partial = 6/9 acceptable, first ship-ready output snow s303). Iter 16 (depth + filled dome) traded multi-pom for vessel — net neutral (1/0/8). Pattern locked: dome shape near canvas-bottom triggers bowl prior regardless of mode.
Fixture: 1 entry × 3 biomes × 3 seeds = **n=9** per iteration.
Reviewer: cold-start sub-agent per TMP_009 §12 (canonical).

---

## 📌 PO hypothesis (2026-05-23, session close — for future asset session)

PO observation after 16 iterations:
> "Vấn đề chính nằm ở model tạo ảnh gốc không sạch, có lẽ nên chọn Flux 1 dev — nặng nhưng mang lại chất lượng tốt nhất."

Reasoning: every iteration so far (except iter 1-2) used SDXL bases (rFantasy / Illustrious / Hezi / Pony / DreamShaper Lightning). All hit a ceiling around 0/9/0 partial (iter 6) or 1/5/3 (iter 15 with ControlNet). The structural intervention helped (iter 15 first V=2 clean) but plateaued. PO hypothesis: the cleanliness ceiling may be a property of the SDXL family priors (training set heavily contains compositional scenes). **Flux 1 dev** — the full model not the Q8 GGUF distilled — may produce cleaner isolated outputs at the cost of much slower gen (~3-5 min/image vs current ~6s). Untested in this session.

Counter-evidence: iter 1 Flux NAG (Q8 GGUF + dark_fantasy LoRA) gave 0/3/6 — worse than iter 15. But that was Flux dev distilled at CFG=1.0 needing NAG; full Flux 1 dev at CFG=3.5 with normal negative is a different beast. Worth a focused spike if a future session reopens asset work.

## 🔴 Session-close decision (2026-05-23)

After 16 iterations of asset spike, **PO calls pause** on asset generation work. Rationale (verbatim): "đã đi rất xa nhưng không có gì để kiểm chứng cả" — have iterated far without an integration target. Next session pivots back to:
- **Tilemap backend** (`services/tilemap-service` Rust, DESIGN.md Phase 4+ HTTP service surface)
- **Frontend plan preparation** — design Phaser 3 client consuming the tilemap output
- Use **free assets OR existing `outputs/homm3-bundle/pass-full-001`** (2445 generated PNGs sitting in this repo) to build a first **real playable map** — validate end-to-end, prove the architecture, before further asset polish.

Iter 15 spike pack + ControlNet arc workflow remain as the working asset baseline if/when asset work resumes.

---

## ⭐ Working baseline (peak after 9 iterations)

| Field | Value |
|---|---|
| Pack JSON | [`realisticfantasy-promptv2-bush-spike-pack.json`](realisticfantasy-promptv2-bush-spike-pack.json) |
| Model alias | `terrain-realisticfantasy-v30` |
| Workflow | `workflows/sdxl_eps.json` (SDXL eps, pre-existing) |
| LoRA stack | **none** (zero LoRA) |
| Sampler / scheduler | euler_ancestral / karras |
| Steps | 28 |
| CFG | 6.0 |
| Negative additions | anti-multi-object + anti-diorama + anti-scene (see pack) |
| Positive template change | dropped "map", forced "single specimen", iso 2.5D viewing angle |
| `prompt_subject` change | dropped "evergreen" (→ tree shape); now "low rounded shrub mass no trunk" |
| **Speed** | **~6 s/image** (RTX 4090) — ~9× faster than Flux NAG baseline |
| **Quality (sub-agent strict)** | **0 clean / 9 partial / 0 fail** ⭐ — only iteration with zero outright fails |
| **Best per-image** | snow_frost s303 + s202 ("closest to drop on canonical tile") |
| Known residual issues | undergrowth tuft 9/9 (C3 ⚠); grassland palette inversion 3/9 (C4 ⚠) |

This is THE pack to start from for any next-iteration spike. Do not invent a
new variant from scratch — patch this one.

---

## Iteration tally (chronological)

| # | Date | Config | Speed | C / P / F | Verdict |
|---|---|---|:-:|:-:|---|
| 1 | 05-22 | **Flux NAG baseline** — Flux dev Q8 + dark_fantasy LoRA + NAGuidance + cfg=1.0 + hardened neg | 57 s | 0 / 3 / 6 | baseline |
| 2 | 05-23 | Flux NAG + post-VAE upscale chain (2x-AnimeSharpV4 → lanczos 1024) | 177 s | 0 / 4 / 5 | REJECT — 3× cost, sharpness "below visual threshold" |
| 3 | 05-23 | terrain-illustrious-xl-v1 (Illustrious base) + isty03 + NewFantasyCoreV4_ILL char LoRA | 11 s | 0 / 0 / 9 | REJECT — anime-face collapse (Illustrious anime prior + character LoRA both wrong) |
| 4 | 05-23 | terrain-realisticfantasy-v30 + isty03 only (drop char LoRA) | 7 s | 0 / 1 / 8 | REJECT — isty03 LoRA is a diorama-composition LoRA (multi-object on tile) |
| 5 | 05-23 | rFantasy zero-LoRA (drop isty03) | 6 s | 0 / 0 / 9 | REJECT — without iso LoRA, base goes to sprite-sheet / landscape scenes |
| **6** | **05-23** | **rFantasy zero-LoRA + prompt rewrite (drop "map", force "single specimen")** | **6 s** | **0 / 9 / 0** | **PEAK — promoted ⭐** |
| 7 | 05-23 | iter 6 + anti-undergrowth / anti-bonsai positive + negative additions | 5 s | 0 / 0 / 9 | REJECT — negation paradox: forbidden concepts AMPLIFIED |
| 8 | 05-23 | iter 6 + iter 7's explicit color biome hints (hybrid) | 5 s | 0 / 4 / 5 | REJECT — long biome hints contain implicit composition tokens → destabilize |
| 9 | 05-23 | heziUltimateJapaneseAndKorean SDXL + iter 6 prompt (PO probe) | 6 s | 0 / 2 / 7 | REJECT — character/portrait/idol base collapses to characters (predicted, confirmed) |
| 10 | 05-23 | DreamShaper XL Lightning (CFG=2.0, 6 steps, DPM++ SDE) + iter 6 prompt (PO probe) | 4 s | 0 / 0 / 9 | REJECT — content-focused but heavy diorama + sprite-sheet bias; Lightning low CFG weakens negative |
| 11 | 05-23 | Pony Diffusion V6 XL (CFG=7, 28 steps, euler_a) + iter 6 prompt (PO probe) | 6 s | 0 / 0 / 9 | REJECT — character collapse 3rd confirmation (5/9 humanoids, anime/cartoon characters). Heuristic #1 locked. |
| 12 | 05-23 | Stage 1 sanity check for "decouple gen+projection" — iter 6 prompt with "iso 2.5D" → "front view straight-on" | 7 s | 0 / 0 / 9 | REJECT — undergrowth bias is subject-level not camera-level; front-view did NOT escape it. Sub-agent: "a 3D lifter will reconstruct the platform/grass as part of the asset." NVS Stage 2 pivot abandoned. |
| 13 | 05-23 | Subject prompt rewrite "topiary specimen + studio product photography" — single-variable test of subject-level bias hypothesis | 5 s | 0 / 0 / 9 | REJECT — language ceiling confirmed. Traded undergrowth ⚠ → pots/pedestals/sprite-sheets ❌. C4 palette 9/9 ⭐ (only positive). Sub-agent: "Ceiling hit; needs structural intervention." |
| 14 | 05-23 | **FIRST STRUCTURAL** — ControlNet xinsir-union-promax canny mode, hint = centred 720×520 white ellipse outline, strength=0.7 end=0.5 | 6 s | 0 / 4 / 5 | **DIRECTIONALLY RIGHT** (first C2/C3 ✅ wins since iter 6: C2 4/9, C3 3/9) but new failure mode = ellipse outline reads as ceramic bowl rim (3/9 explicit dishes). Sub-agent: "Try open-top arc instead — closed ellipse semantically reads as container." |
| **15** | **05-23** | ControlNet canny, hint = OPEN-TOP arc (180° semicircle, no bottom closure) | 6 s | **1 / 5 / 3 ⭐ BEST** | **🎉 FIRST V=2 CLEAN** (snow s303 = "drop-in tile-ready"). Bowls gone. New failure modes: multi-pom bouquet (3/9), substrate disc (3/9). **6/9 acceptable — highest of any structural variant.** |
| 16 | 05-23 | ControlNet **depth mode** + filled-dome hint | 6 s | 1 / 0 / 8 | Sideways — multi-pom GONE (C2 jumped 2/9→8/9 ⭐), **BUT bowls returned 5/9** (depth-volume + filled rounded shape = same vessel prior as iter 14 closed ellipse). Lesson: shape+position triggers vessel prior independent of mode. |

---

## Lessons confirmed twice (don't repeat the mistake)

| Pattern | Confirmed by | Heuristic |
|---|---|---|
| **Character-trained SDXL bases collapse to characters** regardless of prompt strength or anti-character negatives | **iter 3 (Illustrious anime faces) + iter 9 (hezi ice fairies) + iter 11 (Pony V6 XL: 5/9 humanoid characters)** — **3× confirmed, locked** | If checkpoint name contains "anime", "character", "japanese", "korean", "portrait", "girl", "waifu", **"pony", "furry", "chibi"**, or country-prefix → it is character-trained. Do not pick it for prop generation. |
| **Long biome hints destabilize composition** | iter 7 (full hints + anti-X) + iter 8 (just the long color hints) | Biome hint should be SHORT (a colour adjective is enough). Concept tokens like "twisted thorn-knot leaves" / "leafy bundle masses" / "frost-rimmed dormant twigs" compete with "single specimen" in the positive prompt. |
| **Prompt-engineering has a sweet spot** | iter 6 peak; iter 7+8 over-tightened and regressed | Adding more constraints past iter 6 monotonically regresses quality. Plateau is real. |
| **Negation paradox** in diffusion negatives | iter 7 (anti-undergrowth → undergrowth on 9/9) | Strongly negating a concrete visual concept can amplify it. Keep negatives broad-category (terrain, ground, scene) not specific-attribute (no undergrowth, no trunk). |
| **Distilled / Lightning / Turbo SDXL variants amplify base composition priors** | iter 10 (DreamShaper XL Lightning diorama+sheet 9/9) | Low CFG (1-2) of distilled models weakens negative prompts (cf Flux dev CFG=1.0 needed NAG). Combined with rich base composition priors → failure mode amplified. For prop isolation, prefer NON-distilled SDXL bases with normal CFG (5-7). |
| **Undergrowth / ground bias is at the SUBJECT level, not the camera-angle level** | iter 12 (front-view sanity check 0/0/9; same C3 fail as iter 6 + worse verdict distribution) | The `alpine_dwarf_shrub_cluster` subject phrase carries a botanical-illustration prior (shrub on natural ground with visible trunk + base foliage). Camera-angle adjectives ("iso 2.5D" vs "front view") are thin modifiers that don't unbind it. Decouple-gen-projection pivot won't help; subject-level prompt rewrite or structural intervention (ControlNet) needed. |
| **Language-only intervention has a hard ceiling — every prompt move TRADES failure modes** | iter 13 (subject rewrite "topiary + product photo" → pots, pedestals, sprite-sheets, bonsai morphology; 0/0/9) | The space of "plant subject with absolutely no implied context" is essentially absent from SDXL training data. Any plant-naming token activates a contextual prior — botanical-illustration (iter 6), bonsai-photography (iter 13), forest-scene (iter 5), etc. **Path forward = structural** (ControlNet, SAM2, custom LoRA), not more language. |

---

## Multi-model strategy verdict

- **Speed win confirmed**: 6 s/img vs Flux's 57 s/img = **~9.4× faster**. Full 2445-image bundle projection: ~4 GPU-h vs ~39 GPU-h.
- **Quality verdict**: matched-not-beaten on the absolute (0/9/0 partial ties Flux NAG's 0/3/6 on outright fails, beats it on partial count, but no iteration reached V=2 clean).
- **Multi-model approach validated as a direction, not as a complete solution.**

---

## What does NOT work (catalog of rejected configs, to save future iterations)

| Don't try | Why |
|---|---|
| Flux post-VAE upscale-then-downsample | Architecturally wrong: lanczos downsample throws away the upscale model's learned detail. ~3× cost for invisible quality change. |
| Illustrious-XL base | Anime character prior dominates regardless of prompt. iter 3 = 9/9 fail. |
| heziUltimateJapaneseAndKorean | Character/portrait base. iter 9 = 7/9 fail (snow 100% character collapse). |
| `NewFantasyCoreV4_ILL` LoRA | Character-fantasy LoRA, reinforces character bias. iter 3. |
| `game/asset/isty03_style` LoRA alone with strong prompt | Encodes diorama-composition (tile + multi-object). iter 4. |
| Anti-X clauses in positive ("NO undergrowth, NO bonsai, NO trunk") | Negation paradox — amplifies the forbidden concepts. iter 7. |
| Long biome hints with concept tokens | Compete with single-subject framing. iter 7+8. |
| Including the word "map" in the positive prompt | Triggers landscape/scene interpretation. (iter 5 confirmed by removing it in iter 6.) |
| DreamShaper XL Lightning distilled variant | Diorama (stone-rim disc + bonsai) + sprite-sheet (tiled neighbors / inset thumbnails) on 9/9. iter 10. |
| Pony Diffusion V6 XL (and any Pony-family fine-tune) | Character collapse — 5/9 explicit humanoid characters (anime girls, chibi figures, bare-midriff boys). 3rd character-base confirmation. iter 11. |
| Decouple-gen-projection pivot via front-view → NVS/image-to-3D Stage 2 | Front-view Stage 1 sanity check showed same undergrowth/ground bias as iso — bias is subject-level, NVS Stage 2 would inherit dirty input → reconstruct ground as part of mesh. iter 12. |

---

## What MIGHT work (untested, ranked cheapest-first)

| Try | Hypothesis | Risk |
|---|---|---|
| **Iter 6 + minimal color-only biome hint** (just "violet" / "green" / "pale-blue") | Pick up the C4 win without iter 7's destabilization | Medium — possibly stable if hint stays under N tokens |
| **Phase B — Flux+dark_fantasy refiner img2img low-denoise on iter 6 outputs** | Polish iter 6 partial outputs toward HoMM3 painterly style. Use Flux ONLY as stylize pass, not horse-do-all | Cost: +20-30 s/image. May lift some ⚠ to ✅ on style criteria. |
| **Iter 6 + ControlNet base-plate** (canny outline of iso diamond as control image, force single-subject layout via structure not prompt) | Structural enforcement is stronger than prompt | Requires workflow build (~20 min). High likelihood of fixing C3. |
| **SAM2 text-prompted post-process** ("shrub only, no ground") on iter 6 outputs | Semantic cutout > RMBG semantic cutout > white threshold | TMP-ASSET-Q11 in TMP_009 spec. Untested. |
| Different **non-distilled** content-focused SDXL base (`terrain-sdxl-base`, `chroma-hd-q8`) with iter 6 prompt — **skip Lightning/Turbo variants** | rFantasy may not be the only viable base | Cheap; restricted to non-distilled per iter 10 lesson |

---

## Where the prompt sweet spot landed (verbatim, from iter 6 pack)

**Positive template:**
> A single isolated bush sprite asset for a fantasy game, ONE specimen alone, the {subject}, centered on a pure white empty background with nothing else, no landscape, no scene, no multiple plants, no other objects, no ground tile, viewed from an isometric 2.5D camera angle, Heroes of Might and Magic III inspired hand-painted readability, biome palette cue applied only to foliage colours: {biome_hint}

**`prompt_subject` (alpine_dwarf_shrub_cluster):**
> compact alpine dwarf low rounded shrub mass no trunk ground-level cluster wind hardy needles fantasy bush

**Negative (anti-multi-object + anti-diorama + anti-scene categories):**
> multiple objects, sprite sheet, asset pack, character sheet, collection, catalog, grid layout, forest, grove, landscape, scene, panorama, multiple plants, two plants, three plants, many plants, ground, soil, dirt, rocks, stones, pebbles, gravel, sand patch, mud puddle, grass tuft, grass carpet, grass patch, moss patch, dirt mound, snow patch, iso platform, isometric tile base, diamond base, plinth, pedestal, base, terrain, floor, paving, brick, stone wall, fence, border, frame, cast shadow on ground, dropped shadow, ambient occlusion patch beneath prop, exposed roots in soil, root ball, foreground debris, wooden tray, planter box, blurry, soft focus, oversharpen, noisy texture, seamless terrain tile panorama, aerial satellite, tree taller than canvas, person, human, character, face, body, anatomy, creature, monster, nsfw, nude, dragon, mounted unit, army, watermark, logo, text

**Per-biome hints (kept simple, content-only words):**
> chaos_rift: chaos cavern palette, bruised violet fungal shrub curls twisted charcoal stems angular thorn knots readable fantasy
> grassland_temperate: temperate palette, healthy leafy bundle masses readable berry tint accents
> snow_frost: cold palette, frost rimmed dormant twig bundles pale blue needle accents

**Run with default `flux1-dev-q8-tree-nag` swapped for `terrain-realisticfantasy-v30` and CFG/sampler matching the model defaults** (CFG=6.0, euler_ancestral, karras, 28 steps).
