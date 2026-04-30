from __future__ import annotations

import hashlib
import hmac


def sign_payload(timestamp: int, body: bytes, secret: str) -> str:
    msg = f"{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest


def build_signature_header(timestamp: int, digest_hex: str) -> str:
    return f"t={timestamp},v1={digest_hex}"

