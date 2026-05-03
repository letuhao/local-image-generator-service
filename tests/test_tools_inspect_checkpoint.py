"""Tests for tools/inspect_checkpoint.py (safetensors header parsing)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from tools.inspect_checkpoint import bucket_tensor, read_safetensors_index, report_inspect


def _minimal_safetensors(path: Path) -> None:
    """Write a tiny valid safetensors file (two tensors)."""
    meta = {"format": "test"}
    header = {
        "__metadata__": meta,
        "a.weight": {
            "dtype": "F32",
            "shape": [2, 2],
            "data_offsets": [0, 16],
        },
        "cond_stage.text": {
            "dtype": "BF16",
            "shape": [4],
            "data_offsets": [16, 24],
        },
    }
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    blob = b"\x00" * 24
    with path.open("wb") as f:
        f.write(struct.pack("<Q", len(raw)))
        f.write(raw)
        f.write(blob)


def test_read_safetensors_index_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "m.safetensors"
    _minimal_safetensors(p)
    meta, entries = read_safetensors_index(p)
    assert meta == {"format": "test"}
    assert len(entries) == 2
    names = {e.name for e in entries}
    assert names == {"a.weight", "cond_stage.text"}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("model.diffusion_model.input.weight", "diffusion"),
        ("cond_stage_model.transformer.weight", "text/cond"),
        ("first_stage_model.decoder.conv.weight", "vae-ish"),
    ],
)
def test_bucket_tensor(key: str, expected: str) -> None:
    assert bucket_tensor(key) == expected


def test_report_inspect_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "m.safetensors"
    _minimal_safetensors(p)
    code = report_inspect(p, top_n=5, as_json=True)
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tensor_count"] == 2
    assert out["by_bucket_bytes"]

