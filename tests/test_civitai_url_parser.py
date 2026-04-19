from __future__ import annotations

import pytest

from app.loras.civitai_url import (
    ParsedCivitaiUrl,
    parse_civitai_url,
    sanitize_slug,
)


def test_page_with_version_query() -> None:
    got = parse_civitai_url("https://civitai.com/models/123?modelVersionId=456")
    assert got == ParsedCivitaiUrl(host="civitai.com", model_id=123, version_id=456)


def test_page_with_slug_and_version() -> None:
    got = parse_civitai_url("https://civitai.com/models/123/cool-slug?modelVersionId=456")
    assert got == ParsedCivitaiUrl(host="civitai.com", model_id=123, version_id=456)


def test_red_host_accepted() -> None:
    got = parse_civitai_url("https://civitai.red/models/123?modelVersionId=456")
    assert got == ParsedCivitaiUrl(host="civitai.red", model_id=123, version_id=456)


def test_api_download_shape_has_no_model_id() -> None:
    got = parse_civitai_url("https://civitai.com/api/download/models/456")
    assert got == ParsedCivitaiUrl(host="civitai.com", model_id=None, version_id=456)


def test_bare_models_path_without_version_rejected() -> None:
    with pytest.raises(ValueError, match="version_id required"):
        parse_civitai_url("https://civitai.com/models/123")


def test_non_civitai_host_rejected() -> None:
    with pytest.raises(ValueError, match="host not in allowlist"):
        parse_civitai_url("https://example.com/models/123?modelVersionId=456")


def test_http_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="scheme must be https"):
        parse_civitai_url("http://civitai.com/models/123?modelVersionId=456")


def test_uppercase_host_normalized_to_lowercase() -> None:
    got = parse_civitai_url("https://CIVITAI.COM/models/123?modelVersionId=456")
    assert got.host == "civitai.com"


def test_suffix_host_impersonation_rejected() -> None:
    with pytest.raises(ValueError, match="host not in allowlist"):
        parse_civitai_url("https://civitai.com.evil.com/models/123?modelVersionId=456")


def test_subdomain_host_rejected() -> None:
    with pytest.raises(ValueError, match="host not in allowlist"):
        parse_civitai_url("https://evil.civitai.com/models/123?modelVersionId=456")


def test_userinfo_rejected() -> None:
    with pytest.raises(ValueError, match="userinfo"):
        parse_civitai_url("https://user:pass@civitai.com/models/123?modelVersionId=456")


def test_wrong_case_query_param_rejected() -> None:
    with pytest.raises(ValueError, match="version_id required"):
        parse_civitai_url("https://civitai.com/models/123?ModelVersionId=456")


def test_explicit_port_rejected() -> None:
    with pytest.raises(ValueError, match="must not specify a port"):
        parse_civitai_url("https://civitai.com:8443/models/123?modelVersionId=456")


def test_empty_string_rejected() -> None:
    with pytest.raises(ValueError):
        parse_civitai_url("")


def test_sanitize_slug_strips_extension() -> None:
    assert sanitize_slug("AhegaoSlider.safetensors") == "AhegaoSlider"


def test_sanitize_slug_collapses_bad_chars() -> None:
    assert sanitize_slug("foo bar (1).safetensors") == "foo_bar_1"


def test_sanitize_slug_never_empty() -> None:
    assert sanitize_slug(".safetensors") == "unnamed"
    assert sanitize_slug("___") == "unnamed"


def test_sanitize_slug_preserves_allowed_chars() -> None:
    assert sanitize_slug("abc_def-ghi.v2.safetensors") == "abc_def-ghi.v2"
