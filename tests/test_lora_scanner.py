from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.loras.scanner import LoraMeta, scan_loras


def _make_safetensors(path: Path, size: int = 128) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


def test_empty_directory_returns_empty_list(tmp_path: Path) -> None:
    assert scan_loras(tmp_path) == []


def test_missing_root_returns_empty_list(tmp_path: Path) -> None:
    assert scan_loras(tmp_path / "does-not-exist") == []


def test_flat_safetensors_without_sidecar(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "foo.safetensors", size=256)
    metas = scan_loras(tmp_path)
    assert len(metas) == 1
    m = metas[0]
    assert isinstance(m, LoraMeta)
    assert m.name == "foo"
    assert m.filename == "foo.safetensors"
    assert m.sha256 is None
    assert m.source == "local"
    assert m.trigger_words == ()
    assert m.size_bytes == 256
    assert m.addressable is True
    assert m.reason is None
    assert m.sidecar_status == "missing"


def test_flat_with_sidecar(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "style.safetensors")
    (tmp_path / "style.json").write_text(
        json.dumps(
            {
                "sha256": "abc123",
                "source": "civitai",
                "civitai_model_id": 42,
                "civitai_version_id": 99,
                "base_model_hint": "SDXL",
                "trigger_words": ["triggerA", "triggerB"],
                "fetched_at": "2026-04-19T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    metas = scan_loras(tmp_path)
    assert len(metas) == 1
    m = metas[0]
    assert m.sha256 == "abc123"
    assert m.source == "civitai"
    assert m.civitai_model_id == 42
    assert m.civitai_version_id == 99
    assert m.base_model_hint == "SDXL"
    assert m.trigger_words == ("triggerA", "triggerB")
    assert m.fetched_at == "2026-04-19T12:00:00Z"
    assert m.addressable is True


def test_subdirectory_uses_posix_path_in_name(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "hanfu" / "bar.safetensors")
    metas = scan_loras(tmp_path)
    assert len(metas) == 1
    assert metas[0].name == "hanfu/bar"
    assert metas[0].filename == "hanfu/bar.safetensors"
    assert metas[0].addressable is True


def test_space_in_filename_marked_unaddressable(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "foo bar.safetensors")
    metas = scan_loras(tmp_path)
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "foo bar"
    assert m.addressable is False
    assert m.reason is not None
    assert "disallowed characters" in m.reason


def test_crdownload_and_part_files_skipped(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "good.safetensors")
    (tmp_path / "incomplete.safetensors.crdownload").write_bytes(b"0")
    (tmp_path / "incomplete.part").write_bytes(b"0")
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    metas = scan_loras(tmp_path)
    assert [m.name for m in metas] == ["good"]


def test_malformed_sidecar_falls_back_to_minimal_meta(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "foo.safetensors")
    (tmp_path / "foo.json").write_text("{broken json", encoding="utf-8")
    metas = scan_loras(tmp_path)
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "foo"
    assert m.sha256 is None
    assert m.source == "local"
    assert m.trigger_words == ()
    assert m.sidecar_status == "malformed"


def test_non_dict_sidecar_treated_as_malformed(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "foo.safetensors")
    (tmp_path / "foo.json").write_text("[1, 2, 3]", encoding="utf-8")
    metas = scan_loras(tmp_path)
    assert metas[0].sidecar_status == "malformed"


def test_oversized_sidecar_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_safetensors(tmp_path / "foo.safetensors")
    # Swap out the cap so we don't actually have to write 1+ MiB.
    monkeypatch.setattr("app.loras.scanner._SIDECAR_MAX_BYTES", 16)
    (tmp_path / "foo.json").write_text(json.dumps({"sha256": "a" * 256}), encoding="utf-8")
    metas = scan_loras(tmp_path)
    assert metas[0].sidecar_status == "oversized"
    assert metas[0].sha256 is None


def test_sidecar_unknown_source_coerced_to_local(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "foo.safetensors")
    (tmp_path / "foo.json").write_text(
        json.dumps({"source": "uploaded", "sha256": "abc"}), encoding="utf-8"
    )
    metas = scan_loras(tmp_path)
    m = metas[0]
    assert m.source == "local"
    assert m.sha256 == "abc"
    assert m.sidecar_status == "ok"


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CLAUDE_TESTS_ALLOW_SYMLINK"),
    reason="Windows symlink creation needs Developer Mode or admin; opt in via env.",
)
def test_scanner_skips_entries_escaping_root_via_symlink(tmp_path: Path) -> None:
    """Scanner must refuse to list files reachable only via directory symlinks
    that resolve outside `root`. Matches validator's realpath-containment defense
    so GET /v1/loras never leaks filenames from elsewhere on disk."""
    root = tmp_path / "loras"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked.safetensors").write_bytes(b"\x00")
    link_dir = root / "link"
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported on this host: {exc}")

    metas = scan_loras(root)
    names = {m.name for m in metas}
    assert "link/leaked" not in names
    assert not any("leaked" in m.name for m in metas)


def test_results_sorted_by_name(tmp_path: Path) -> None:
    _make_safetensors(tmp_path / "zebra.safetensors")
    _make_safetensors(tmp_path / "alpha.safetensors")
    _make_safetensors(tmp_path / "mid" / "middle.safetensors")
    names = [m.name for m in scan_loras(tmp_path)]
    assert names == sorted(names)
