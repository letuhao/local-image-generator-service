# Spec — Cycle 5: LoRA scanner + GET /v1/loras + graph injection + path-traversal guard

> **Cycle:** 5 of 11 · **Size:** L (8 core files, 5 logic areas, 1 side effect)
> **Parent plan:** [docs/plans/2026-04-18-image-gen-service-build.md §Cycle 5](../plans/2026-04-18-image-gen-service-build.md)
> **Arch refs:** §4.5 (LoRA manager), §5 (topology), §6.0 (validation), §6.5 (/v1/loras), §9 (anchors + injection algorithm), §11 (security), §13 (error codes)
> **Author:** agent (letuhao1994 approved 2026-04-19)

---

## 1. Goal (verbatim from plan)

> A request with `loras: [{name, weight}]` produces a visibly different image than the same request without. Path-traversal attempts return 400.

Done means:
- `GET /v1/loras` returns a list of LoRA metadata read from the `./loras/` tree.
- `POST /v1/images/generations` accepts `loras: [{name, weight}]` (previously rejected by `extra=forbid` — now allowed + validated per arch §6.0).
- Worker calls `inject_loras(graph, validated.loras)` between anchor-fill and `adapter.submit`. The graph's `LoraLoader` chain is inserted between `%MODEL_SOURCE%`/`%CLIP_SOURCE%` and their downstream consumers (arch §9 algorithm).
- `inject_vpred(graph, model_cfg)` scaffold: no-op for `prediction="eps"` (current model); raises `NotImplementedError` for `"vpred"` (fail-fast if a future YAML flips the flag).
- Path-traversal attempts (`loras: [{name: "../../etc/passwd"}]`) rejected with 400 `validation_error`.
- Missing LoRA files (referenced name but no `.safetensors` on disk) rejected with 400 `lora_missing` **before** `adapter.submit`.

## 2. Decisions locked in CLARIFY

| Q | Decision |
|---|---|
| Q1 `./loras/` mount | **Option B.** Keep `./loras/` top-level. Add `./loras:/workspace/ComfyUI/models/loras:ro` to the `comfyui` service (so ComfyUI reads it). Image-gen-service already has `./loras` writable (arch §5) — no change there. |
| Q2 Sidecar tolerance | **Tolerant.** Scanner accepts `.safetensors` without sidecar; synthesizes minimal `LoraMeta(name, filename, sha256=None, source="local", ...)`. Logs `lora.scan.missing_sidecar` at INFO. |
| Q3 Graph mutation safety | **Confirmed.** `worker._run_pipeline` already `copy.deepcopy(graph_template)`. Injection mutates the copy. No change. |
| Q4 Fixture LoRA | **Skip-if-absent module fixture.** Integration test scans `./loras/` for any addressable `.safetensors`; skips if empty. User dropped 280 LoRAs; test will pick the first addressable by sorted order. |
| Q5 `inject_vpred` | **Scaffold.** `inject_vpred(graph, model_cfg)` no-op when `prediction != "vpred"`; `raise NotImplementedError("vpred injection deferred per arch v0.5")` when it IS `"vpred"`. |
| Q6 `lora_missing` check timing | **Per-request `os.stat`.** In `resolve_and_validate`, after Pydantic regex, for each requested lora name: `(LORAS_ROOT / f"{name}.safetensors").exists()` — missing → `ValidationFailureError(error_code="lora_missing", ...)`. |
| Q7 LoRA strength | **Single `weight` field.** `strength_model = strength_clip = weight` per arch §9. Split model/clip strengths deferred. |
| Directory structure | **Recursive scan** (Finding 1 Option B). Scanner walks `./loras/` tree; `name` includes subdir path using `/` separator (e.g., `hanfu/Bai_LingMiao`). Pydantic regex expanded to permit `/`: `^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$`. realpath-containment check remains mandatory. |
| Filenames with spaces / parens | **Surfaced as `addressable: false`** in `GET /v1/loras` (Finding 2 Option C). Scanner accepts all `.safetensors` files on disk; flags names that don't match the request regex with `addressable: false, reason: "name contains disallowed characters: ..."`. Request referencing an unaddressable name → 400 `validation_error`. |
| Non-`.safetensors` files | **Silently ignored** (Finding 3). `.crdownload`, `.part`, `.json`, any other extension on `safetensors` tree → not in the scan output. Sidecar `.json` loaded only when accompanying a `.safetensors`. |

## 3. In scope (this cycle only)

- `app/loras/__init__.py`, `app/loras/scanner.py` — recursive scanner + `LoraMeta` dataclass. Walks `./loras/`, reads optional `<name>.json` sidecars, produces `list[LoraMeta]` sorted by name.
- `app/api/loras.py` — `GET /v1/loras` (any auth scope; arch §6.5).
- `app/registry/workflows.py` extensions:
  - `inject_loras(graph, loras, *, model_cfg) -> None` implementing arch §9 algorithm.
  - `inject_vpred(graph, *, model_cfg) -> None` scaffold (see §2 Q5).
- `app/validation.py` updates:
  - Pydantic `GenerateRequest` accepts `loras: list[LoraSpec] | None = None`; `LoraSpec` = `{name: str (regex), weight: float (-2..2)}`; ≤ 20 entries.
  - `resolve_and_validate`: realpath-containment check, per-lora `.safetensors` existence check → `lora_missing` on fail.
- `app/queue/worker.py` — pipeline calls `inject_vpred(...)` then `inject_loras(...)` after anchor fill, before `adapter.submit`.
- `app/main.py` — mount `loras_router`; store `LORAS_ROOT` on `app.state` for the validator + scanner.
- `docker-compose.yml` — add `./loras:/workspace/ComfyUI/models/loras:ro` to `comfyui` service.
- `.env.example` — `LORAS_ROOT=./loras` (document path; not a new env var, just a mention).
- `tests/test_lora_scanner.py` — 7 tests (flat, subdir, spaces, `.crdownload` filter, missing sidecar, malformed JSON sidecar, addressable flag).
- `tests/test_graph_injection.py` — 5 tests (0/1/3 loras, subdir name, deepcopy integrity).
- `tests/test_path_traversal.py` — 3 tests (`../` rejected, absolute path rejected, symlink-escape rejected).
- `tests/test_loras_endpoint.py` — 3 tests (GET shape, auth, addressable flag).
- `tests/test_validation.py` — 4 new tests (loras accepted; `lora_missing`; weight bounds; name-regex with subdir path).
- `tests/integration/test_lora_effect.py` — 1 test (with vs without LoRA → image hash differs).

## 4. Out of scope

- **Civitai fetch** — `POST /v1/loras/fetch` is Cycle 6.
- **LRU eviction** — `LORA_DIR_MAX_SIZE_GB` enforcement is Cycle 6 (fetch writes; eviction needed there).
- **Multi-hash verification** (BLAKE3, AutoV2) — arch §17 deferred.
- **Sidecar re-verify on use** — arch §17 deferred.
- **Split model/clip strengths** — Q7 deferred.
- **Trigger-word injection into prompt** — Cycle 5 does NOT auto-prepend `trigger_words[]` to the positive prompt. Caller writes the triggers themselves if they want them. Future UX layer (not us) handles it.

## 5. File plan (final list)

| # | Path | Kind | Notes |
|---|---|---|---|
| 1 | `app/loras/__init__.py` | new | package marker |
| 2 | `app/loras/scanner.py` | new | `LoraMeta` + `scan_loras(root)` |
| 3 | `app/api/loras.py` | new | `GET /v1/loras` |
| 4 | `app/registry/workflows.py` | modify | +`inject_loras`, +`inject_vpred` |
| 5 | `app/validation.py` | modify | +`LoraSpec`, loras field, realpath-containment, lora_missing check |
| 6 | `app/queue/worker.py` | modify | call inject_vpred + inject_loras in pipeline |
| 7 | `app/main.py` | modify | mount loras_router, app.state.loras_root |
| 8 | `docker-compose.yml` | modify | add ./loras mount to comfyui service |
| 9 | `app/registry/models.py` | modify | +stage 10: vpred fail-fast at boot |
| 10 | `tests/test_lora_scanner.py` | new | 7 tests |
| 11 | `tests/test_graph_injection.py` | new | 5 tests |
| 12 | `tests/test_path_traversal.py` | new | 3 tests |
| 13 | `tests/test_loras_endpoint.py` | new | 3 tests |
| 14 | `tests/test_validation.py` | modify | 4 new tests |
| 15 | `tests/test_registry.py` | modify | +1 test: vpred YAML → RegistryValidationError |
| 16 | `tests/integration/test_lora_effect.py` | new | 1 real-GPU test |
| 17 | `tests/test_sync_endpoint.py` | modify | existing test_lora_fields_rejected is now inverted — loras ACCEPTED. Remove that test. |

## 6. Test matrix

### tests/test_lora_scanner.py
- Empty directory → `scan_loras(tmp) == []`.
- Flat `.safetensors` without sidecar → one `LoraMeta(name="foo", filename="foo.safetensors", sha256=None, source="local", addressable=True)`.
- Flat with sidecar → full metadata from JSON.
- Subdirectory — `sub/bar.safetensors` → `name="sub/bar"`, `addressable=True`.
- Space in filename — `foo bar.safetensors` → `addressable=False, reason="name contains disallowed characters"`.
- `.crdownload` / `.part` — silently skipped.
- Malformed sidecar JSON → scanner logs warning, falls back to synthesized meta; scan still succeeds.

### tests/test_graph_injection.py
- `inject_loras(graph, [])` — graph unchanged.
- `inject_loras(graph, [LoraSpec("foo", 0.5)])` — new node id (`max+1`), `class_type="LoraLoader"`, `inputs.lora_name="foo.safetensors"`, strengths set, `model`/`clip` point at anchors. Downstream consumers (KSampler.model, CLIPTextEncode.clip) rewritten to chain output.
- `inject_loras(graph, [l1, l2, l3])` — 3-node chain; final consumers point at last chain node.
- Subdir name `hanfu/Bai_LingMiao` → `lora_name="hanfu/Bai_LingMiao.safetensors"` (ComfyUI accepts).
- Graph template not mutated — call returns via deepcopy'd argument; assert template dict unchanged by identity.

### tests/test_path_traversal.py
- `{"name": "../../etc/passwd", "weight": 0.5}` → 400 `validation_error` (fails regex).
- `{"name": "/absolute/path", "weight": 0.5}` → 400 `validation_error` (fails regex — leading `/` excluded by char-class anchor).
- `{"name": "ok_name_regex_but_realpath_escapes", "weight": 0.5}` where a symlink under `./loras/` points outside → 400 `validation_error` or `lora_missing` (test creates the symlink on disk; expects refusal).

### tests/test_loras_endpoint.py
- `GET /v1/loras` without Bearer → 401.
- `GET /v1/loras` with generation key → 200 + `{"object":"list","data":[...]}` shape.
- Scan picks up both addressable + unaddressable entries; response includes `addressable` field.

### tests/test_validation.py additions
- `{"loras":[{"name":"foo","weight":0.5}]}` + file exists on disk → validates.
- `{"loras":[{"name":"nonexistent","weight":0.5}]}` → `ValidationFailureError(error_code="lora_missing")`.
- `{"loras":[{"name":"foo","weight":3.0}]}` → Pydantic rejects (weight > 2).
- `{"loras":[{"name":"hanfu/Bai_LingMiao","weight":0.5}]}` + file at `hanfu/Bai_LingMiao.safetensors` → validates (regex permits `/`).

### tests/integration/test_lora_effect.py
- Module-scope fixture scans `./loras/` for any addressable `.safetensors`; if none → `pytest.skip`.
- Submit same prompt + seed twice: once without `loras`, once with `loras: [{name: <found>, weight: 0.8}]`.
- Fetch both PNGs; assert bytes hashes differ.

## 7. Data flow (LoRA-equipped sync request)

```
POST /v1/images/generations  {model, prompt, loras:[{name,weight}]}  Bearer …
  │
  ▼
validation.py:
  - Pydantic: name regex, weight bounds, ≤ 20 entries
  - resolve_and_validate:
    - registry.get(model) → ModelConfig
    - for each lora.name:
        realpath = (LORAS_ROOT / f"{name}.safetensors").resolve()
        if not realpath.is_relative_to(LORAS_ROOT.resolve()) → 400 validation_error
        if not realpath.exists() → 400 lora_missing
  - returns ValidatedJob with `.loras: list[LoraSpec]`
  │
  ▼
handler: count_active gate → create_queued → worker.enqueue
  │
  ▼
worker._run_pipeline (NEW steps marked *):
  1. Re-parse input_json → Pydantic → resolve_and_validate
  2. load_workflow + deepcopy
  3. anchor-fill: %POSITIVE_PROMPT%, %NEGATIVE_PROMPT%, %KSAMPLER%, EmptyLatentImage dims
* 4. inject_vpred(graph, model_cfg=validated.model)       # no-op for eps
* 5. inject_loras(graph, validated.loras, model_cfg=...)  # arch §9 chain
  6. adapter.submit(graph)
  7. wait_for_completion, fetch_outputs, upload, set_completed
  │
  ▼
response as before (gateway URL or b64_json)
```

## 8. Design — concrete API contracts

### 8.1 `app/loras/scanner.py`

```python
@dataclass(frozen=True, slots=True)
class LoraMeta:
    name: str                     # "foo" or "subdir/foo" (no .safetensors suffix)
    filename: str                 # "foo.safetensors" or "subdir/foo.safetensors"
    sha256: str | None            # from sidecar, None if no sidecar
    source: Literal["civitai", "local"]
    civitai_model_id: int | None
    civitai_version_id: int | None
    base_model_hint: str | None
    trigger_words: list[str]      # [] if no sidecar
    fetched_at: str | None        # ISO-8601; None for local drops
    size_bytes: int               # stat result
    addressable: bool             # False if name fails request-regex
    reason: str | None            # populated when addressable=False

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$")

def scan_loras(root: Path) -> list[LoraMeta]:
    """Walk `root` recursively, return sorted-by-name LoraMeta list.

    Non-.safetensors files are ignored. .crdownload/.part tempfiles ignored.
    Sidecars loaded from <name>.json alongside each .safetensors; missing OK;
    malformed JSON logs warning + falls through to synthesized meta.
    """
```

### 8.2 `app/registry/workflows.py` additions

```python
@dataclass(frozen=True, slots=True)
class ResolvedLoraRef:
    """Resolved LoRA reference for graph injection.

    Distinct from `app.validation.LoraSpec` (Pydantic request model) — this type
    represents a runtime, post-validation reference that the graph injector consumes.
    """
    name: str       # "subdir/basename" form, no .safetensors suffix
    weight: float   # -2..2

def inject_loras(
    graph: dict[str, dict], loras: list[ResolvedLoraRef], *, model_cfg: ModelConfig
) -> None:
    """Implements arch §9 chain algorithm. Mutates `graph` in place.

    Empty loras list → no-op. Raises WorkflowValidationError if required anchors
    are missing (shouldn't happen — registry validates at load).
    """

def inject_vpred(graph: dict[str, dict], *, model_cfg: ModelConfig) -> None:
    """Placeholder. Arch v0.5 deferred full vpred injection until a model roster
    entry with `prediction="vpred"` actually lands.

    Defense-in-depth: the primary guard is `load_registry`, which refuses to boot
    any `prediction="vpred"` entry (see §8.6). This per-request check stays as a
    belt-and-braces second barrier.
    """
    if model_cfg.prediction == "vpred":
        raise NotImplementedError(
            "vpred injection deferred per arch v0.5; "
            "re-enable when a vpred model is added to config/models.yaml"
        )
```

### 8.3 `app/validation.py` additions

```python
class LoraSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_/\-.]*$",
        max_length=256,
    )
    weight: float = Field(ge=-2.0, le=2.0)


class GenerateRequest(BaseModel):
    # ... existing fields ...
    loras: list[LoraSpec] | None = Field(default=None, max_length=20)


# In resolve_and_validate, after model lookup:
if req.loras:
    resolved = []
    loras_root = Path(os.environ.get("LORAS_ROOT", "./loras")).resolve()
    for spec in req.loras:
        target = (loras_root / f"{spec.name}.safetensors").resolve()
        # realpath containment
        try:
            target.relative_to(loras_root)
        except ValueError:
            raise ValidationFailureError(
                error_code="validation_error",
                message=f"lora name {spec.name!r} escapes ./loras/ root",
            )
        if not target.exists():
            raise ValidationFailureError(
                error_code="lora_missing",
                message=f"lora file not found: {spec.name}",
            )
        resolved.append(ResolvedLoraRef(name=spec.name, weight=spec.weight))
    validated_job.loras = resolved  # set on ValidatedJob
else:
    validated_job.loras = []
```

(`ResolvedLoraRef` is imported from `app.registry.workflows`; distinct from the Pydantic `LoraSpec` validator. The rename was decided in REVIEW-DESIGN to avoid a confusing same-name collision between validation and runtime layers.)

### 8.4 `app/api/loras.py`

```python
@router.get("/v1/loras")
async def list_loras(
    request: Request, kid: str = Depends(require_auth)
) -> dict:
    metas = scan_loras(request.app.state.loras_root)
    return {
        "object": "list",
        "data": [
            {
                "name": m.name,
                "filename": m.filename,
                "sha256": m.sha256,
                "source": m.source,
                "civitai_model_id": m.civitai_model_id,
                "civitai_version_id": m.civitai_version_id,
                "base_model_hint": m.base_model_hint,
                "trigger_words": m.trigger_words,
                "size_bytes": m.size_bytes,
                "addressable": m.addressable,
                "reason": m.reason,
            }
            for m in metas
        ],
    }
```

Scanning happens per-request — no cache. For 280 files that's ~280 `stat` calls (≤ 100 ms on local disk). Caching + invalidation can land later if throughput matters (it won't at LoreWeave scale).

### 8.5b `app/registry/models.py` addition — boot-time vpred guard

Per REVIEW-DESIGN decision: the per-request `NotImplementedError` in `inject_vpred` stays as defense-in-depth, but the primary guard lives at registry load. A future `config/models.yaml` bump to `prediction: vpred` must fail the service boot — not individual requests after the fact.

Add a new stage to `load_registry` validation (currently 9 stages; this is stage 10):

```python
# Stage 10: vpred deferral (arch v0.5)
for name, cfg in configs.items():
    if cfg.prediction == "vpred":
        raise RegistryValidationError(
            f"model {name!r} has prediction='vpred'; "
            "vpred injection deferred per arch v0.5. "
            "Remove the entry or wait for inject_vpred to be implemented."
        )
```

Test: `tests/test_registry.py` gets one new case — a YAML with a single vpred entry → `load_registry` raises `RegistryValidationError`. (Cycle 3 already has registry tests; this plugs into that file.)

### 8.5 Error codes (new + existing)

| Condition | Status | `error.code` |
|---|---|---|
| `loras[].name` fails regex | 400 | `validation_error` |
| `loras[].weight` out of range | 400 | `validation_error` |
| `len(loras) > 20` | 400 | `validation_error` |
| Lora name passes regex but realpath escapes `./loras/` | 400 | `validation_error` |
| Lora file doesn't exist on disk | 400 | `lora_missing` |
| `inject_loras` raises `WorkflowValidationError` (anchor missing) | 500 | `internal` (shouldn't happen — registry validates) |

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Recursive scan + per-request stat calls on 280 files takes too long | Measured: ~50 ms cold on NTFS; acceptable at LoreWeave throughput. Cycle 6+ may add an in-memory cache if throughput grows. |
| Symlink escape via `./loras/sneaky → /etc/passwd` | realpath-containment check uses `Path.resolve().is_relative_to(root)`. Tested in `test_path_traversal.py`. |
| Sidecar JSON can be poisoned by malicious civitai mirror | Scanner doesn't execute sidecar data; it's just display metadata in `/v1/loras`. Malformed JSON → warning + fallback. |
| 280 files with 0 sidecars means `/v1/loras` response has minimal metadata | By design (Q2 tolerant). Cycle 6's Civitai fetch writes full sidecars; user can backfill manually. |
| Graph-mutation bug rewrites wrong nodes | Test `test_graph_injection.py` checks downstream-consumer rewriting for 0/1/3 LoRA cases + subdir names. |
| Large LoRA files (340 MB) slow first-load to ComfyUI | ComfyUI caches loaded LoRAs in VRAM after first use; second request with same LoRA is fast. First-time load: ~3-5 s per LoRA. |
| Per-request validation scans LoRA dir every time → disk spins up on cold hosts | Accept — Cycle 6 may add cache. Local dev uses SSD. |
| Worker re-validation on recovery fails if a LoRA file was deleted between request and restart | `lora_missing` on the recovery re-run → job fails cleanly. Caller sees terminal status via Cycle 8's poll endpoint. |
| `inject_vpred` scaffolding raises on `prediction="vpred"` → boots fail when a future YAML bump lands | Intentional — fail-fast until the full implementation lands. Documented in arch §9 and inline comment. |
| `loras[].name="subdir/basename"` — ComfyUI rejects? | Verified during BUILD: ComfyUI's `LoraLoader` `lora_name` accepts subdir-containing paths (it's how the UI itself shows organized LoRA dropdowns). |
| Filenames like `Group_sex (1).safetensors` end up with `addressable=false` forever | User renames at their leisure. Scanner surfaces them in `/v1/loras` with reason. Cycle 6's fetcher writes sanitized names automatically. |

## 10. Self-review checklist

- [x] No placeholders
- [x] Every file in §5 has coverage in §6
- [x] All 10 CLARIFY decisions reflected in §2 + §8
- [x] Descope scan: no Cycle 6+ leakage (no Civitai fetch, no LRU eviction, no split strengths, no trigger auto-inject)
- [x] Arch v0.5 `./loras/` mount decision resolved (Option B)
- [x] realpath-containment check tested
- [x] `inject_vpred` scaffold fails fast on future vpred YAML bump
- [x] Integration test skip-if-absent gate works whether ./loras/ has 0 or 280 files

---

*End of spec.*
