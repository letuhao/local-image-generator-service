#!/usr/bin/env bash
set -euo pipefail

# Download/verify model artifacts referenced by config/models.yaml.
# Requires: python + huggingface-cli (for hf:// sources).

MODELS_YAML_PATH="${MODELS_YAML_PATH:-config/models.yaml}"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-downloads}"
export MODELS_YAML_PATH
export DOWNLOADS_DIR

if [[ ! -f "$MODELS_YAML_PATH" ]]; then
  echo "ERROR: models yaml not found: $MODELS_YAML_PATH"
  exit 1
fi

mkdir -p "$DOWNLOADS_DIR"

before_bytes="$(python - <<'PY'
from pathlib import Path
import os

p = Path(os.environ["DOWNLOADS_DIR"])
total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
print(total)
PY
)"

python - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

import yaml


def parse_hf_spec(value: str, *, field: str) -> tuple[str, str]:
    spec = value.removeprefix("hf://")
    parts = spec.split("/")
    if len(parts) < 3:
        raise SystemExit(f"Invalid hf:// format for {field}: {value!r}")
    repo_id = "/".join(parts[:2])
    file_path = "/".join(parts[2:])
    return repo_id, file_path


yaml_path = Path(os.environ["MODELS_YAML_PATH"])
downloads_dir = Path(os.environ["DOWNLOADS_DIR"])
doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
models = doc.get("models") or []

hf_entries: list[tuple[str, str, Path]] = []
for model in models:
    name = model.get("name", "unknown")
    for key in ("checkpoint", "vae", "clip_l", "t5xxl"):
        value = model.get(key)
        if not isinstance(value, str):
            continue
        if value.startswith("hf://"):
            repo, file_path = parse_hf_spec(value, field=f"{name}.{key}")
            dest = downloads_dir / Path(file_path).name
            hf_entries.append((repo, file_path, dest))

if not hf_entries:
    print("No hf:// entries found in config/models.yaml; nothing to download.")
    raise SystemExit(0)

for repo, file_path, dest in hf_entries:
    print(f"{repo}/{file_path} -> {dest}")
PY

# Second pass: perform downloads with retries.
python - <<'PY'
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import yaml


def parse_hf_spec(value: str, *, field: str) -> tuple[str, str]:
    spec = value.removeprefix("hf://")
    parts = spec.split("/")
    if len(parts) < 3:
        raise SystemExit(f"Invalid hf:// format for {field}: {value!r}")
    repo_id = "/".join(parts[:2])
    file_path = "/".join(parts[2:])
    return repo_id, file_path


yaml_path = Path(os.environ["MODELS_YAML_PATH"])
downloads_dir = Path(os.environ["DOWNLOADS_DIR"])
doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
models = doc.get("models") or []

entries: list[tuple[str, str, Path]] = []
for model in models:
    name = model.get("name", "unknown")
    for key in ("checkpoint", "vae", "clip_l", "t5xxl"):
        value = model.get(key)
        if isinstance(value, str) and value.startswith("hf://"):
            repo, file_path = parse_hf_spec(value, field=f"{name}.{key}")
            entries.append((repo, file_path, downloads_dir / Path(file_path).name))

for repo, file_path, dest in entries:
    if dest.exists():
        print(f"SKIP existing: {dest}")
        continue
    ok = False
    for attempt in range(1, 4):
        print(f"Downloading {repo}/{file_path} (attempt {attempt}/3)")
        cmd = [
            "huggingface-cli",
            "download",
            repo,
            file_path,
            "--local-dir",
            str(downloads_dir),
            "--local-dir-use-symlinks",
            "False",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            ok = True
            break
        print(proc.stdout.strip())
        print(proc.stderr.strip())
        time.sleep(2 * attempt)
    if not ok:
        raise SystemExit(f"Failed download after retries: {repo}/{file_path}")
PY

after_bytes="$(python - <<'PY'
from pathlib import Path
import os

p = Path(os.environ["DOWNLOADS_DIR"])
total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
print(total)
PY
)"

delta="$((after_bytes - before_bytes))"
echo "Downloads size delta: ${delta} bytes (before=${before_bytes}, after=${after_bytes})"
