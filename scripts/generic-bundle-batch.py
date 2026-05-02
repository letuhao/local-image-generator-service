#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request

def _post_binary(
    base_url: str, api_key: str, payload: dict, timeout_s: float
) -> tuple[bytes, str | None]:
    req = request.Request(
        f"{base_url.rstrip('/')}/v1/images/generations/binary",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}" if api_key else "Bearer ",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    with request.urlopen(req, timeout=timeout_s) as resp:
        content = resp.read()
        return content, resp.headers.get("X-Job-Id")

def _should_retry_without_transparency(http_status: int, body: str) -> bool:
    if http_status != 400:
        return False
    return "extra inputs are not permitted" in body.lower()

class BundleConfig:
    """Nạp cấu hình global của Bundle (Categories & Biomes)"""
    def __init__(self, bundle_dir: Path):
        self.bundle_dir = bundle_dir
        cat_path = self.bundle_dir / "asset_categories.json"
        loc_path = self.bundle_dir / "locations.json"
        
        self.categories = []
        if cat_path.exists():
            self.categories = json.loads(cat_path.read_text(encoding="utf-8")).get("categories", [])
            
        self.biomes = []
        if loc_path.exists():
            self.biomes = json.loads(loc_path.read_text(encoding="utf-8")).get("biomes", [])
            
        self.category_lookup = {c["id"]: c for c in self.categories}
        self.biome_lookup = {b["id"]: b for b in self.biomes}

class JobBuilder:
    """Quét manifest và xây dựng luồng công việc (Jobs) theo Seeds và Rotations"""
    def __init__(self, config: BundleConfig, out_dir: Path, args: argparse.Namespace):
        self.config = config
        self.out_dir = out_dir
        self.args = args
        
        self.seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
        self.rotations = [r.strip() for r in args.rotations.split(",") if r.strip()]
        self.target_categories = [c.strip() for c in getattr(args, "categories", "").split(",")] if getattr(args, "categories", "") else []
        
        
        self.rotation_prompts = {
            "0": "viewed from front, facing forward",
            "90": "viewed from the right side, profile view",
            "180": "viewed from behind, back view",
            "270": "viewed from the left side, profile view"
        }
        
        # Parse LoRAs: "name1:0.8,name2:1.0"
        self.loras = []
        if args.loras:
            for item in args.loras.split(","):
                item = item.strip()
                if not item: continue
                if ":" in item:
                    name, weight = item.split(":", 1)
                    self.loras.append({"name": name.strip(), "weight": float(weight.strip())})
                else:
                    self.loras.append({"name": item.strip(), "weight": 1.0})

    def discover_jobs(self) -> list[dict]:
        jobs = []
        locations_dir = self.config.bundle_dir / "locations"
        if not locations_dir.exists():
            print(f"Locations directory not found: {locations_dir}", file=sys.stderr)
            return jobs
            
        for manifest_path in locations_dir.rglob("manifest.json"):
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Error reading {manifest_path}: {e}", file=sys.stderr)
                continue
                
            location = manifest_data.get("location", "unknown_loc")
            category_id = manifest_data.get("category", manifest_path.parent.name)
            biome_id = manifest_data.get("biome_id", "unknown_biome")
            
            if self.target_categories and category_id not in self.target_categories:
                continue
            
            
            category_info = self.config.category_lookup.get(category_id, {})
            biome_info = self.config.biome_lookup.get(biome_id, {})
            
            cat_keywords = category_info.get("prompt_keywords", "")
            biome_hint = biome_info.get("hint", "")
            
            for asset in manifest_data.get("assets", []):
                tile_size = asset.get("tile_size", "1x1")
                base_prompt = asset.get("prompt", "")
                negative_prompt = asset.get("negative_prompt", "")
                
                width = 1024
                height = 1024
                if "dimensions" in asset:
                    width = asset["dimensions"].get("width", width)
                    height = asset["dimensions"].get("height", height)
                
                # Nhân bản Job: Seeds x Rotations
                for rot in self.rotations:
                    for seed in self.seeds:
                        # Ánh xạ góc xoay thành Keyword cho Prompt
                        rot_keyword = self.rotation_prompts.get(rot, f"facing {rot} degrees") if rot != "0" else self.rotation_prompts.get("0", "viewed from front")
                        
                        prompt_parts = [base_prompt]
                        if biome_hint: prompt_parts.append(biome_hint)
                        if cat_keywords: prompt_parts.append(cat_keywords)
                        if rot_keyword: prompt_parts.append(rot_keyword)
                        
                        final_prompt = ", ".join(p for p in prompt_parts if p)
                        
                        filename_base = f"{asset['id']}__r{rot}__s{seed}"
                        
                        target_dir = self.out_dir / location / category_id
                        target_png = target_dir / f"{filename_base}.png"
                        target_json = target_dir / f"{filename_base}.json"
                        
                        payload_dict = {
                            "model": self.args.model_override,
                            "prompt": final_prompt,
                            "negative_prompt": negative_prompt,
                            "size": f"{width}x{height}",
                            "steps": self.args.steps,
                            "cfg": self.args.cfg,
                            "seed": seed,
                            "transparent_background": True # Khuyến nghị cho game assets
                        }
                        
                        if self.loras:
                            payload_dict["loras"] = self.loras
                            
                        jobs.append({
                            "asset_id": asset["id"],
                            "target_png": target_png,
                            "target_json": target_json,
                            "payload": payload_dict,
                            "llm_token": {
                                "token_version": "1.0",
                                "asset": {
                                    "id": asset["id"],
                                    "name": asset.get("name", ""),
                                    "category": category_id,
                                    "biome": biome_id,
                                    "tags": asset.get("tags", [])
                                },
                                "map_attributes": {
                                    "tile_size": tile_size,
                                    "rotation_degrees": rot,
                                    "is_seamless": asset.get("seamless", False)
                                },
                                "generation_meta": {
                                    "seed": seed,
                                    "model": self.args.model_override,
                                    "loras": self.loras,
                                    "prompt_used": final_prompt,
                                    "image_dimensions": {"width": width, "height": height}
                                }
                            }
                        })
                        
            # --- PROCESS CHARACTERS ---
            for char in manifest_data.get("characters", []):
                char_id = char["id"]
                appearance = char.get("appearance_prompt", "")
                negative_prompt = char.get("negative_prompt", "")
                outputs = char.get("outputs", {})
                
                for out_key, out_cfg in outputs.items():
                    if not out_cfg.get("enabled", False):
                        continue
                        
                    width = out_cfg.get("dimensions", {}).get("width", 1024)
                    height = out_cfg.get("dimensions", {}).get("height", 1024)
                    style_prompt = out_cfg.get("style_prompt", "")
                    
                    # Only apply rotation matrix to sprites. Portraits/Concepts are always "0"
                    applicable_rotations = self.rotations if out_key == "sprite" else ["0"]
                    
                    for rot in applicable_rotations:
                        for seed in self.seeds:
                            prompt_parts = [appearance, style_prompt]
                            
                            if rot != "0":
                                rot_keyword = self.rotation_prompts.get(rot, f"facing {rot} degrees")
                                prompt_parts.append(rot_keyword)
                                
                            final_prompt = ", ".join(p for p in prompt_parts if p)
                            
                            # Example filename: di_xin_dialog_portrait__r0__s101.png
                            filename_base = f"{char_id}_{out_key}__r{rot}__s{seed}"
                            
                            target_dir = self.out_dir / location / "characters" / char_id
                            target_png = target_dir / f"{filename_base}.png"
                            target_json = target_dir / f"{filename_base}.json"
                            
                            payload_dict = {
                                "model": self.args.model_override,
                                "prompt": final_prompt,
                                "negative_prompt": negative_prompt,
                                "size": f"{width}x{height}",
                                "steps": self.args.steps,
                                "cfg": self.args.cfg,
                                "seed": seed,
                                "transparent_background": True
                            }
                            if self.loras:
                                payload_dict["loras"] = self.loras
                                
                            jobs.append({
                                "asset_id": char_id,
                                "target_png": target_png,
                                "target_json": target_json,
                                "payload": payload_dict,
                                "llm_token": {
                                    "token_version": "1.0",
                                    "character": {
                                        "id": char_id,
                                        "name": char.get("name", ""),
                                        "class": char.get("class", ""),
                                        "faction": manifest_data.get("faction", ""),
                                        "tags": char.get("tags", [])
                                    },
                                    "map_attributes": {
                                        "output_type": out_key,
                                        "rotation_degrees": rot
                                    },
                                    "generation_meta": {
                                        "seed": seed,
                                        "model": self.args.model_override,
                                        "loras": self.loras,
                                        "prompt_used": final_prompt,
                                        "image_dimensions": {"width": width, "height": height}
                                    }
                                }
                            })
                            
        return jobs

class GenerationEngine:
    """Thực thi gọi API sinh ảnh và xuất file PNG + JSON Token"""
    def __init__(self, args: argparse.Namespace):
        self.args = args

    def run(self, jobs: list[dict]):
        total = len(jobs)
        failures = 0
        skipped = 0
        
        for idx, job in enumerate(jobs):
            target_png = job["target_png"]
            target_json = job["target_json"]
            payload = job["payload"]
            
            if self.args.resume and target_png.exists() and target_png.stat().st_size > 0:
                print(f"[SKIP] {idx+1}/{total} -> {target_png.name} already exists.")
                skipped += 1
                continue
                
            target_png.parent.mkdir(parents=True, exist_ok=True)
            
            if self.args.dry_run:
                print(f"[DRY] {idx+1}/{total} -> {target_png.name}")
                continue
                
            try:
                png_bytes, job_id = _post_binary(self.args.base_url, self.args.api_key, payload, self.args.timeout_s)
                target_png.write_bytes(png_bytes)
                
                # Ghi JSON Token Sidecar
                token_data = job["llm_token"]
                token_data["generation_meta"]["job_id"] = job_id
                token_data["generation_meta"]["image_path"] = target_png.name
                token_data["generation_meta"]["generated_at"] = datetime.now(UTC).isoformat()
                
                target_json.write_text(json.dumps(token_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"[OK] {idx+1}/{total} -> {target_png.name}")
                
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                
                # Xử lý fallback nếu API không hỗ trợ background trong suốt (VD: Base Flux)
                if _should_retry_without_transparency(exc.code, body):
                    retry_payload = dict(payload)
                    retry_payload.pop("transparent_background", None)
                    try:
                        png_bytes, job_id = _post_binary(self.args.base_url, self.args.api_key, retry_payload, self.args.timeout_s)
                        target_png.write_bytes(png_bytes)
                        
                        token_data = job["llm_token"]
                        token_data["generation_meta"]["job_id"] = job_id
                        token_data["generation_meta"]["image_path"] = target_png.name
                        token_data["generation_meta"]["generated_at"] = datetime.now(UTC).isoformat()
                        token_data["generation_meta"]["fallback_no_transparency"] = True
                        
                        target_json.write_text(json.dumps(token_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                        print(f"[OK] {idx+1}/{total} -> {target_png.name} (fallback: no transparent_background)")
                    except Exception as retry_exc:
                        failures += 1
                        print(f"[FAIL] {idx+1}/{total} -> {target_png.name} error: {retry_exc}", file=sys.stderr)
                else:
                    failures += 1
                    print(f"[FAIL] {idx+1}/{total} -> {target_png.name} HTTP {exc.code}: {body}", file=sys.stderr)
            except Exception as exc:
                failures += 1
                print(f"[FAIL] {idx+1}/{total} -> {target_png.name} error: {exc}", file=sys.stderr)
                
        print(f"\nFinished. Total planned: {total}, Skipped: {skipped}, Failures: {failures}")
        return 1 if failures else 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Generic Bundle Batch Generator")
    parser.add_argument("--bundle-dir", required=True, help="Path to bundle root (e.g., map_bundles/investiture-of-the-gods)")
    parser.add_argument("--out-dir", required=True, help="Output root directory")
    
    # Tham số Sinh ảnh
    parser.add_argument("--model-override", default="flux1-dev", help="Model name to use")
    parser.add_argument("--steps", type=int, default=28, help="Inference steps")
    parser.add_argument("--cfg", type=float, default=1.0, help="Guidance scale")
    
    # Tham số Execution Multipliers (Ma trận)
    parser.add_argument("--seeds", type=str, default="101", help="Comma-separated seeds (e.g., '101,102')")
    parser.add_argument("--rotations", type=str, default="0", help="Comma-separated rotations in degrees (e.g., '0,90,180,270')")
    parser.add_argument("--categories", type=str, default="", help="Comma-separated categories to generate (e.g., 'characters,artifacts'). Empty means all.")
    parser.add_argument("--loras", type=str, default="", help="Comma-separated loras, format: 'name1:weight1,name2:weight2'")
    
    # Cấu hình API Service
    parser.add_argument("--base-url", default="http://127.0.0.1:8700", help="Service base URL")
    parser.add_argument("--api-key", default="", help="Generation API key")
    parser.add_argument("--timeout-s", type=float, default=360.0, help="Per-request timeout")
    
    # Cờ (Flags)
    parser.add_argument("--dry-run", action="store_true", help="Print plan only, no API calls")
    parser.add_argument("--resume", action="store_true", help="Skip existing non-empty PNG files")
    
    args = parser.parse_args()
    
    if not args.dry_run and not str(args.api_key).strip():
        print("[WARNING] No --api-key provided. Local proxy might reject the request if it strictly requires one.", file=sys.stderr)

    bundle_path = Path(args.bundle_dir).resolve()
    out_path = Path(args.out_dir).resolve()
    
    config = BundleConfig(bundle_path)
    builder = JobBuilder(config, out_path, args)
    jobs = builder.discover_jobs()
    
    if not jobs:
        print("No jobs found. Check if manifest.json files exist in the locations directory.")
        return 0
        
    engine = GenerationEngine(args)
    return engine.run(jobs)

if __name__ == "__main__":
    sys.exit(main())
