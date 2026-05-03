# Operator tools

Small CLIs used **outside** the API container (or from the repo with `uv run`). They are not imported by `app/` at runtime.

## `inspect_checkpoint.py`

Safetensors **header-only** inspection for very large checkpoints (AIO bundles, fp8 merges). Does not load tensor payloads into RAM.

```bash
uv run python tools/inspect_checkpoint.py inspect models/checkpoints/your_model.safetensors
uv run python tools/inspect_checkpoint.py inspect ./models/**/*.safetensors
uv run python tools/inspect_checkpoint.py inspect model.safetensors --top 25 --json
uv run python tools/inspect_checkpoint.py hints
```

Use this to see approximate **which parts of the key space dominate** (diffusion vs text vs VAE-ish names) before choosing a smaller distributed build (GGUF stack, pruned fp8, etc.).
