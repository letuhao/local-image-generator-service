import os
import json
import re

md_path = r"D:\Works\source\local-image-generator-service\map_bundles\investiture-of-the-gods\locations\zhaoge\decadent_capital\TASK_GAP_FILL.md"
out_dir = r"D:\Works\source\local-image-generator-service\map_bundles\investiture-of-the-gods\locations\zhaoge\decadent_capital"

with open(md_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find all code blocks
blocks = re.findall(r'```(.*?)```', content, re.DOTALL)

assets_by_category = {}

for block in blocks:
    lines = block.strip().split('\n')
    asset = {}
    current_key = None
    prompt_lines = []
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith("ID:"): asset["id"] = line.split(":", 1)[1].strip()
        elif line.startswith("Name:"): asset["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Category:"): asset["category"] = line.split(":", 1)[1].strip()
        elif line.startswith("Format:"): asset["format"] = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"): asset["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("Prompt:"):
            current_key = "prompt"
        elif line.startswith("Negative:"):
            current_key = None
            asset["negative_prompt"] = line.split(":", 1)[1].strip()
        elif line.startswith("Color Palette:"):
            current_key = None
            palette_str = line.split(":", 1)[1].strip()
            try:
                asset["color_palette"] = json.loads(palette_str)
            except:
                pass
        elif line.startswith("Tags:"):
            current_key = None
            tags_str = line.split(":", 1)[1].strip()
            # Parse [tag1, tag2]
            tags_str = tags_str.strip("[]")
            asset["tags"] = [t.strip() for t in tags_str.split(",")]
        elif line.startswith("Priority:"):
            current_key = None
            asset["priority"] = line.split(":", 1)[1].strip()
        else:
            if current_key == "prompt":
                prompt_lines.append(line)
    
    if prompt_lines:
        asset["prompt"] = " ".join(prompt_lines)
        
    if "id" in asset and "category" in asset:
        cat = asset["category"]
        
        # default dimensions based on format
        if asset.get("format") == "orthographic_top_down":
            asset["dimensions"] = {"width": 1024, "height": 1024}
            asset["tile_size"] = "2x2"
        else:
            asset["dimensions"] = {"width": 512, "height": 512}
            asset["tile_size"] = "1x1"
            
        asset["seamless"] = False
        
        if cat not in assets_by_category:
            assets_by_category[cat] = []
        assets_by_category[cat].append(asset)

print(f"Parsed {sum(len(v) for v in assets_by_category.values())} assets.")

for cat, assets in assets_by_category.items():
    cat_dir = os.path.join(out_dir, cat)
    os.makedirs(cat_dir, exist_ok=True)
    manifest_path = os.path.join(cat_dir, "manifest.json")
    
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    else:
        manifest = {
            "location": f"zhaoge/decadent_capital",
            "category": cat,
            "biome_id": "shang_capital",
            "assets": []
        }
    
    # avoid duplicates
    existing_ids = {a["id"] for a in manifest["assets"]}
    added = 0
    for a in assets:
        a_copy = dict(a)
        del a_copy["category"] # removed from individual asset
        if a_copy["id"] not in existing_ids:
            manifest["assets"].append(a_copy)
            added += 1
            
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        
    print(f"Added {added} assets to {manifest_path}")
