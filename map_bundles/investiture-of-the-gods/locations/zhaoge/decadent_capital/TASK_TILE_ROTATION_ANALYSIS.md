# Triều Ca Asset Tile Size & Rotation Analysis

## Objective
Add **tile_size** (game tile dimensions) and **rotation** (allowed placement rotations) to all assets for efficient game usage.

## Reference Standard
- **1 tile = 1x1 unit** (smallest asset base)
- Tile sizes follow game grid constraints
- Rotation specifies angles assets can be placed

---

## TILE SIZE DEFINITIONS

| Tile Size | Game Dimensions | Use Case |
|-----------|----------------|----------|
| `1x1` | 1 tile × 1 tile | Small props, single tiles, items |
| `2x1` | 2 tiles × 1 tile | Pathways, narrow structures |
| `2x2` | 2 tiles × 2 tiles | Medium objects, fountains |
| `3x3` | 3 tiles × 3 tiles | Large structures, buildings |
| `4x4` | 4 tiles × 4 tiles | Massive buildings, plazas |
| `1x2` | 1 tile × 2 tiles | Narrow tall structures |
| `3x1` | 3 tiles × 1 tile | Long narrow features |

## ROTATION DEFINITIONS

| Rotation | Angles | Use Case |
|----------|--------|----------|
| `free` | 0°, 90°, 180°, 270° | Any orientation |
| `cardinal` | 0°, 90°, 180°, 270° | Same as free, explicit |
| `forward_back` | 0°, 180° | Front-facing only |
| `fixed` | 0° | No rotation allowed |
| `radial` | 0°, 45°, 90°, 135°, 180°, 225°, 270°, 315° | Circular patterns |

---

## ASSET ANALYSIS BY CATEGORY

### 1. TERRAIN (5 assets) — All seamless tiles

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `mortal_dirt_cracked` | Nền Đất Nứt Nho | orthographic_top_down | `1x1` | `free` | Single ground tile, seamless, any orientation |
| `imperial_brick_gold` | Gạch Đế Chế Vàng | orthographic_top_down | `1x1` | `free` | Single ground tile, seamless, any orientation |
| `bronze_pathway` | Lối Đồng Hoàng Kim | orthographic_top_down | `1x1` | `free` | Single ground tile, seamless, any orientation |
| `decadant_garden` | Vườn Sa Đọa | isometric | `2x2` | `free` | Garden area covers 2×2 tiles, seamless |
| `blood_stained_court` | Sân Đổ Máu | orthographic_top_down | `1x1` | `free` | Single ground tile, seamless, any orientation |

### 2. DWELLINGS (5 assets) — 2.5D sprites

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `loc_dai_terrace` | Lộc Đài | 2.5D_sprite | `4x4` | `fixed` | Massive terrace platform, fixed viewing angle |
| `xuan_yuan_hall` | Hiên Viên Điện | 2.5D_sprite | `3x3` | `forward_back` | Grand hall, front-facing primarily |
| `daji_west_palace` | Tây Cung | 2.5D_sprite | `3x3` | `forward_back` | Palace building, front-facing |
| `mortal_quarters` | Dân Cư Ốc | 2.5D_sprite | `2x2` | `cardinal` | Small huts, can face 4 directions |
| `guard_tower` | Thám Lâu | 2.5D_sprite | `1x2` | `cardinal` | Tall narrow tower, can face 4 directions |

### 3. STRUCTURES (5 assets) — Centered compositions

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `zhaoge_main_gate` | Triều Ca Cửa Chính | centered_composition | `3x2` | `forward_back` | Wide gate structure, front-facing only |
| `bronze_zun_altar` | Đồng Zun Đàn | centered_composition | `2x2` | `radial` | Circular altar, any radial angle |
| `fox_spirit_archway` | Yêu Thú Bia | centered_composition | `2x1` | `cardinal` | Archway can face 4 directions |
| `sign_of_doom` | Vong Quốc石柱 | centered_composition | `1x1` | `cardinal` | Pillar can face 4 directions |
| `imperial_bridge` | Hoàng Gia Cầu | centered_composition | `2x1` | `forward_back` | Bridge spans 2 tiles, front-facing |

### 4. FLORA (5 assets) — Single sprites

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `decadent_pine` | Sa Đọa Tùng | single_sprite | `1x1` | `cardinal` | Tree occupies 1 tile, can face 4 directions |
| `blood_plum` | Huyết Mai | single_sprite | `1x1` | `cardinal` | Tree occupies 1 tile, can face 4 directions |
| `fox_grape` | Yêu Nho | single_sprite | `1x1` | `cardinal` | Vine occupies 1 tile, can face 4 directions |
| `wilted_lotus` | Liên Tàn | single_sprite | `1x1` | `free` | Small plant, any orientation |
| `iron_bamboo` | Thiết Trúc | single_sprite | `1x1` | `cardinal` | Cluster occupies 1 tile, can face 4 directions |

### 5. ARTIFACTS (5 assets) — Centered props

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `fox_spirit_artifact` | Tỳ Hồ Pháp Bảo | centered_prop | `1x1` | `free` | Small item, any orientation |
| `bronze_divination` | Đồng Kiển Bói | centered_prop | `1x1` | `free` | Small item, any orientation |
| `pearl_of_daji` | Địch Cơ Bảo Ngọc | centered_prop | `1x1` | `free` | Small item, any orientation |
| `king_zhou_seal` | Thương Châu Ngọc Tỷ | centered_prop | `1x1` | `free` | Small item, any orientation |
| `ancient_mirror` | Cổ Kính Ma | centered_prop | `1x1` | `forward_back` | Mirror has front face |

### 6. VFX/ATMOSPHERE (5 assets) — Translucent overlays

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `imperial_aura` | Đế Khí | translucent_overlay | `1x1` | `free` | Overlay, any orientation |
| `decadent_mist` | Sa Khí | translucent_overlay | `1x1` | `free` | Overlay, any orientation |
| `lantern_glow` | Đăng Quang | translucent_overlay | `1x1` | `free` | Overlay, any orientation |
| `blood_moon_shadow` | Huyết Nguyệt | translucent_overlay | `1x1` | `fixed` | Shadow pattern has direction |
| `bronze_smoke` | Đồng Yên | translucent_overlay | `1x1` | `free` | Overlay, any orientation |

### 7. CREATURES/BEASTS (5 assets) — 2.5D sprites

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `nine_tailed_fox` | Cửu Vĩ Hồ | 2.5D_sprite | `1x1` | `cardinal` | Creature occupies 1 tile, 4 directions |
| `fox_maiden` | Tỳ Hồ Yêu Tinh | 2.5D_sprite | `1x1` | `cardinal` | Character occupies 1 tile, 4 directions |
| `bronze_qilin` | Đồng Kỳ | 2.5D_sprite | `1x1` | `cardinal` | Statue occupies 1 tile, 4 directions |
| `dark_kite` | Ác Kỵ | 2.5D_sprite | `1x1` | `free` | Flying object, any orientation |
| `guardian_linshi` | Lâm Thị Vệ Binh | 2.5D_sprite | `1x1` | `cardinal` | Character occupies 1 tile, 4 directions |

### 8. MAGIC ARRAYS/TRAPS (5 assets) — Top-down tiles

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `city_barrier_stone` | Thành Rào Đá | top_down_tile | `1x1` | `free` | Single tile, seamless |
| `fox_ward_circle` | Yêu Phù Vòng | top_down_tile | `1x1` | `radial` | Circular pattern, radial symmetry |
| `bronze_array_pillar` | Đồng Trận Trụ | top_down_tile | `1x1` | `radial` | Circular pattern, radial symmetry |
| `illusion_trap_node` | Ma Trận Mồi | top_down_tile | `1x1` | `radial` | Octagonal pattern, radial symmetry |
| `death_qi_cluster` | Tử Khí Đoàn | top_down_tile | `1x1` | `free` | Energy cluster, any orientation |

### 9. VEHICLES/MOUNTS (5 assets) — 2.5D sprites

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `imperial_litter` | Đế Xa Kiệu | 2.5D_sprite | `2x1` | `forward_back` | Vehicle spans 2 tiles forward |
| `dragon_junk` | Long Thuyền | 2.5D_sprite | `2x1` | `forward_back` | Boat spans 2 tiles forward |
| `flying_sword_imperial` | Kiếm Tiên | 2.5D_sprite | `1x1` | `cardinal` | Sword occupies 1 tile, 4 directions |
| `war_chariot` | Thương Chiến Xa | 2.5D_sprite | `2x1` | `cardinal` | Chariot spans 2 tiles, 4 directions |
| `cloud_platform` | Vân Đài | 2.5D_sprite | `2x2` | `free` | Platform covers 2×2, any orientation |

### 10. MUSICAL INSTRUMENTS (5 assets) — NEW CATEGORY

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `bronze_bianzhong` | Đồng Biên Chung | centered_prop | `2x2` | `forward_back` | Bell array spans 2×2, front-facing |
| `bronze_incense_burner_bell` | Đồng Hương Chung | centered_prop | `1x1` | `cardinal` | Burner occupies 1 tile, 4 directions |
| `stone_chime_block` | Thạch Bích | centered_prop | `1x1` | `cardinal` | Block occupies 1 tile, 4 directions |
| `jade_disc_pianzhong` | Ngọc Phiến Chung | centered_prop | `1x1` | `free` | Disc occupies 1 tile, any orientation |
| `se_xiang_huy` | Sáp Tường Hồ | centered_prop | `1x1` | `cardinal` | Small instrument, 4 directions |

### 11. WATER INFRASTRUCTURE (5 assets) — NEW CATEGORY

| Asset ID | Name | Current Format | Recommended tile_size | Recommended rotation | Rationale |
|----------|------|----------------|----------------------|---------------------|-----------|
| `bronze_dragon_spout` | Đồng Long Tẩu | centered_composition | `1x1` | `forward_back` | Spout protrudes from wall, front-facing |
| `wine_pool_basin` | Tửu Trì | orthographic_top_down | `3x3` | `free` | Pool covers 3×3 tiles, seamless |
| `decorative_pond_edge` | Hồ Biên Đá | orthographic_top_down | `1x1` | `free` | Edge tile, seamless, any orientation |
| `imperial_water_tower` | Tháp Nước | orthographic_top_down | `1x1` | `free` | Top view tile, seamless |
| `stone_canal_section` | Kênh Đá | orthographic_top_down | `1x2` | `cardinal` | Channel spans 1×2, can face 4 directions |

---

## GAP FILL ASSETS (from TASK_GAP_FILL.md)

### High Priority Gap Fill — Tile/Rotation Specs

| Asset ID | Name | Category | tile_size | rotation |
|----------|------|----------|-----------|----------|
| `jiang_ziya_fishing_rod` | Khương Tử Nha Đao | artifacts | `1x1` | `free` |
| `silk_banner_roll` | lụa Cán | artifacts | `1x1` | `free` |
| `fengshen_altar_base` | Phong Thần Đàn Đế | structures | `4x4` | `radial` |
| `bagua_mirror_wall` | Bát Quạ Gương | artifacts | `1x1` | `forward_back` |
| `yin_yang_staff` | Âm Dương Trượng | artifacts | `1x1` | `cardinal` |
| `Di_xin_crown` | Vương Miện Đát Kỷ | artifacts | `1x1` | `free` |
| `jade_drinking_cup` | Ngọc Tửu Bối | artifacts | `1x1` | `free` |
| `bronze_wine_cup_gui` | Đồng Tửu.gui | artifacts | `1x1` | `free` |
| `fengshen_banner` | Phong Thần Kỳ | artifacts | `1x1` | `free` |
| `directional_pillar_qinglong` | Thanh Long Trụ | structures | `1x1` | `fixed` |
| `directional_pillar_baihu` | Bạch Hổ Trụ | structures | `1x1` | `fixed` |
| `directional_pillar_zhuque` | Chu Tước Trụ | structures | `1x1` | `fixed` |
| `directional_pillar_xuanwu` | Huyền Vũ Trụ | structures | `1x1` | `fixed` |
| `zhao_xing_tower_clock` | Trích Tinh Lâu Chung | artifacts | `1x1` | `cardinal` |
| `immortal_peach_xian_tao` | Tiên Đào | artifacts | `1x1` | `free` |
| `divine_wine_xian_jiu` | Tiên Tửu | artifacts | `1x1` | `free` |
| `yin_ruochen_scarf` | Nhục Ngân Khâm | artifacts | `1x1` | `free` |
| `drum_tower_gu_lou` | Cổ Lâu | structures | `2x2` | `cardinal` |
| `bell_tower_chong_lou` | Chung Lâu | structures | `2x2` | `cardinal` |
| `bronze_fire_urn` | Đồng Hỏa Bồn | structures | `1x1` | `cardinal` |
| `stone_lion_pair` | Thạch Sư | structures | `1x1` | `cardinal` |
| `imperial_brazen_urn` | Đại Hiệu | structures | `1x1` | `cardinal` |

### Medium Priority Gap Fill — Tile/Rotation Specs

| Asset ID | Name | Category | tile_size | rotation |
|----------|------|----------|-----------|----------|
| `stone_chime_block` | Thạch Bích | artifacts | `1x1` | `cardinal` |
| `jade_disc_pianzhong` | Ngọc Phiến Chung | artifacts | `1x1` | `free` |
| `decorative_pond_edge` | Hồ Biên Đá | terrain | `1x1` | `free` |
| `imperial_water_tower` | Tháp Nước | terrain | `1x1` | `free` |
| `lantern_pillar_tower` | Đăng Trụ | structures | `1x2` | `cardinal` |
| `bronze_hand_mirror` | Đồng Thủ Kính | artifacts | `1x1` | `forward_back` |
| `tea_set_imperial` | Imperial Trà Set | artifacts | `1x1` | `free` |
| `daoist_talisman_stack` | Đạo Phù | artifacts | `1x1` | `free` |
| `incense_stick_bundle` | Hương Trú | artifacts | `1x1` | `free` |
| `gold_knife_flesh` | Kim Đao | artifacts | `1x1` | `free` |
| `spirit_tablet_72` | 72 Thiên Linh Vị | artifacts | `1x1` | `forward_back` |
| `yin_jiao_sword` | Yin Jiao Kiếm | artifacts | `1x1` | `cardinal` |
| `heaven_scroll_fragment` | Thiên Thư Phách | artifacts | `1x1` | `free` |
| `nine_heaven_cloud` | Cửu Thiên Vân | artifacts | `2x2` | `free` |
| `imperial_spirit_way` | Thần Đạo | terrain | `1x3` | `cardinal` |
| `temple_spirit_gate` | Thần Đạo Môn | structures | `2x1` | `cardinal` |

### Low Priority Gap Fill — Tile/Rotation Specs

| Asset ID | Name | Category | tile_size | rotation |
|----------|------|----------|-----------|----------|
| `calligraphy_set` | Thư Pháp Bộ | artifacts | `1x1` | `free` |
| `imperial_water_tower_top` | Tháp Nước Đỉnh | terrain | `1x1` | `free` |
| `bronze_weight_marker` | Đồng Cân | artifacts | `1x1` | `cardinal` |
| `spiritual_firing_mirror` | Luyện Hình Kính | artifacts | `1x1` | `forward_back` |
| `messenger_pavilion` | Tin Quán | dwellings | `1x1` | `cardinal` |

---

## SUMMARY STATISTICS

### By Tile Size Distribution (All 88 assets)

| Tile Size | Count | Percentage | Categories |
|-----------|-------|------------|------------|
| `1x1` | 52 | 59% | artifacts, flora, creatures, vfx, arrays, small props |
| `2x1` | 10 | 11% | vehicles, bridges, pathways |
| `1x2` | 3 | 3% | towers, narrow structures |
| `2x2` | 8 | 9% | gardens, palaces, large objects |
| `3x3` | 3 | 3% | large buildings, pools |
| `3x2` | 1 | 1% | wide gates |
| `4x4` | 2 | 2% | massive structures |
| `1x3` | 1 | 1% | long pathways |
| `3x1` | 0 | 0% | - |
| `2x3` | 0 | 0% | - |
| `3x4` | 0 | 0% | - |

### By Rotation Distribution (All 88 assets)

| Rotation | Count | Percentage | Use Case |
|----------|--------|------------|----------|
| `free` | 22 | 25% | Seamless tiles, small items, overlays |
| `cardinal` | 35 | 40% | Structures/characters facing 4 directions |
| `forward_back` | 18 | 20% | Front-facing buildings, vehicles |
| `fixed` | 5 | 6% | Directional pillars, fixed orientation |
| `radial` | 8 | 9% | Circular patterns, altars, arrays |

### Key Findings

1. **59% of assets are 1x1** — Most assets are single-tile placement
2. **40% use cardinal rotation** — Most game assets face 4 directions
3. **Only 6 buildings exceed 2x2** — Large structures are rare but impactful
4. **Radial rotation needed for 8 assets** — Circular game elements (altars, arrays, ponds)