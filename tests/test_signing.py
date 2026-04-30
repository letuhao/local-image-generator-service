from __future__ import annotations

from app.webhooks.signing import build_signature_header, sign_payload


def test_sign_payload_known_vector() -> None:
    digest = sign_payload(1713456000, b'{"event":"job.completed"}', "secret123")
    assert digest == "6cbc01b59eb8ee08413bdad04241d45d5a70900601687ecda20bd64e76a3bead"


def test_build_signature_header_format() -> None:
    header = build_signature_header(1713456000, "abc")
    assert header == "t=1713456000,v1=abc"

