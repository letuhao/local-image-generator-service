from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

import structlog
from fastapi import Header, HTTPException, Request, status

log = structlog.get_logger(__name__)

# RFC 6750 §2.1: scheme names are case-insensitive. We lowercase on compare.
_BEARER_PREFIX_LOWER = "bearer "


@dataclass(frozen=True, slots=True)
class _Keyset:
    generation: frozenset[str]
    admin: frozenset[str]


class AuthError(HTTPException):
    def __init__(self, message: str = "missing or invalid credentials") -> None:
        # RFC 7235 §3.1: 401 responses MUST carry a WWW-Authenticate challenge.
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "auth_error", "message": message}},
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthScopeError(HTTPException):
    def __init__(self, message: str = "admin scope required") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "auth_error", "message": message}},
        )


def parse_keys(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(s for s in (part.strip() for part in raw.split(",")) if s)


def kid_for(key: str) -> str:
    """Return the first 8 hex chars of sha256(key). Lowercase by construction.

    8 hex chars = 32 bits. Birthday-bound collision ≈ 2^16 (~65k) distinct keys.
    This is safe for ≤ a few dozen keys. If the service ever grows to per-tenant
    keys at scale, widen to 12+ hex chars and audit the logs for collisions.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def load_keyset_from_env() -> _Keyset:
    return _Keyset(
        generation=parse_keys(os.environ.get("API_KEYS")),
        admin=parse_keys(os.environ.get("ADMIN_API_KEYS")),
    )


def _extract_bearer(authorization: str | None) -> str | None:
    """Return the token part of an `Authorization: Bearer <token>` header.

    Scheme match is case-insensitive per RFC 6750 §2.1 (`Bearer`, `bearer`,
    `BEARER` all accepted). Trailing whitespace on the token is stripped.
    """
    if not authorization:
        return None
    if not authorization[: len(_BEARER_PREFIX_LOWER)].lower() == _BEARER_PREFIX_LOWER:
        return None
    return authorization[len(_BEARER_PREFIX_LOWER) :].strip() or None


def _match_any(candidate: str, keys: frozenset[str]) -> bool:
    """Constant-time compare against each key in the set."""
    found = False
    for k in keys:
        if hmac.compare_digest(candidate, k):
            found = True
            # Don't early-return: keep the compare count stable regardless of position.
    return found


def verify_key(authorization: str | None, keyset: _Keyset) -> bool:
    """Best-effort check: return True iff the header carries a Bearer token that
    matches any key in the combined scope set. Intended for callers that need to
    gate verbose output (e.g. /health) but must not raise on failure.
    Never leaks which scope matched.
    """
    token = _extract_bearer(authorization)
    if token is None:
        return False
    return _match_any(token, keyset.generation) or _match_any(token, keyset.admin)


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    """Accept any valid generation-scope OR admin-scope key. Returns the matched kid."""
    keyset: _Keyset = request.app.state.keyset
    token = _extract_bearer(authorization)
    if token is None:
        log.info("auth.rejected", reason="missing_or_malformed_header")
        raise AuthError()

    if _match_any(token, keyset.generation) or _match_any(token, keyset.admin):
        kid = kid_for(token)
        structlog.contextvars.bind_contextvars(key_id=kid)
        log.info("auth.accepted", scope="any")
        return kid

    log.info("auth.rejected", reason="unknown_key", attempted_kid=kid_for(token))
    raise AuthError()


async def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    """Admin-scope only. Generation key → 403, unknown/missing → 401."""
    keyset: _Keyset = request.app.state.keyset
    token = _extract_bearer(authorization)
    if token is None:
        log.info("auth.rejected", reason="missing_or_malformed_header", scope="admin")
        raise AuthError()

    if _match_any(token, keyset.admin):
        kid = kid_for(token)
        structlog.contextvars.bind_contextvars(key_id=kid)
        log.info("auth.accepted", scope="admin")
        return kid

    if _match_any(token, keyset.generation):
        log.info("auth.rejected", reason="wrong_scope", attempted_kid=kid_for(token))
        raise AuthScopeError()

    log.info("auth.rejected", reason="unknown_key", attempted_kid=kid_for(token))
    raise AuthError()
