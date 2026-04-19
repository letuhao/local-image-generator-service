from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# Two hosts share Civitai's backend — `.com` is the main site, `.red` is the
# NSFW-permissive split. API + download calls always go to `.com` regardless of
# which page URL the admin pasted.
_ALLOWED_HOSTS: frozenset[str] = frozenset({"civitai.com", "civitai.red"})
API_HOST: str = "civitai.com"

_MODELS_PATH_RE = re.compile(r"^/models/(\d+)(?:/[^/?#]*)?$")
_API_DOWNLOAD_PATH_RE = re.compile(r"^/api/download/models/(\d+)$")

_SLUG_CLEAN_RE = re.compile(r"[^A-Za-z0-9_.\-]+")
_SLUG_COLLAPSE_RE = re.compile(r"_+")


@dataclass(frozen=True, slots=True)
class ParsedCivitaiUrl:
    """Parsed Civitai fetch request. `host` is the page host the admin pasted
    (for audit logging); API calls always hit `API_HOST`.

    `model_id` is None on the `/api/download/models/<vid>` direct-download shape,
    since that URL never includes a model id.
    """

    host: str
    model_id: int | None
    version_id: int


def parse_civitai_url(url: str) -> ParsedCivitaiUrl:
    """Strict parser. Raises ValueError on malformed or unsupported input.

    Rejections:
      - non-https schemes
      - any userinfo (`user:pass@...`) — hijack-proofing
      - any explicit port — no `https://civitai.com:8443/...`
      - hosts not in the exact-match allowlist
      - `/models/<id>` without `?modelVersionId=<vid>` query param
      - `modelVersionId` in non-canonical case (`?ModelVersionId=...`)
      - any other path shape
    """
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"scheme must be https, got {parsed.scheme!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain userinfo")
    # urlparse raises ValueError on invalid ports (already handled implicitly);
    # we reject explicit ports even if valid.
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid port in url: {exc}") from exc
    if port is not None:
        raise ValueError("url must not specify a port")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f"host not in allowlist: {host!r}")

    # Direct download shape: /api/download/models/<version_id>
    m = _API_DOWNLOAD_PATH_RE.match(parsed.path)
    if m:
        return ParsedCivitaiUrl(host=host, model_id=None, version_id=int(m.group(1)))

    # Page shape: /models/<model_id>[/<slug>]?modelVersionId=<version_id>
    m = _MODELS_PATH_RE.match(parsed.path)
    if m is None:
        raise ValueError(f"unsupported civitai path: {parsed.path!r}")
    model_id = int(m.group(1))
    qs = parse_qs(parsed.query, keep_blank_values=False)
    vid_values = qs.get("modelVersionId")
    if not vid_values:
        raise ValueError("version_id required; append ?modelVersionId=<id> to the URL")
    if len(vid_values) != 1:
        raise ValueError("modelVersionId must appear exactly once")
    try:
        version_id = int(vid_values[0])
    except ValueError as exc:
        raise ValueError(f"modelVersionId must be an integer, got {vid_values[0]!r}") from exc
    return ParsedCivitaiUrl(host=host, model_id=model_id, version_id=version_id)


def sanitize_slug(filename: str) -> str:
    """Derive a filesystem-safe slug from a Civitai `files[].name`.

    Strips `.safetensors` suffix, collapses runs of disallowed chars to `_`,
    and trims leading/trailing separator noise. Always returns a non-empty
    string (`"unnamed"` fallback) so the save path never becomes ambiguous.
    """
    if filename.endswith(".safetensors"):
        filename = filename[: -len(".safetensors")]
    cleaned = _SLUG_CLEAN_RE.sub("_", filename)
    cleaned = _SLUG_COLLAPSE_RE.sub("_", cleaned)
    cleaned = cleaned.strip("_.")
    return cleaned or "unnamed"
