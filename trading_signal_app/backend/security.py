import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict


class SignedTokenError(ValueError):
    pass


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _signing_secret() -> bytes:
    secret = (
        os.environ.get("BROKER_AUTH_STATE_SECRET")
        or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
        or ""
    ).strip()
    if not secret:
        raise RuntimeError(
            "BROKER_AUTH_STATE_SECRET or CREDENTIAL_ENCRYPTION_KEY must be configured"
        )
    return secret.encode("utf-8")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_signed_token(payload: Dict[str, Any], ttl_seconds: int) -> str:
    now = int(time.time())
    body = dict(payload)
    body.setdefault("iat", now)
    body.setdefault("exp", now + ttl_seconds)
    body.setdefault("nonce", secrets.token_urlsafe(12))
    encoded = _encode(
        json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(_signing_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def verify_signed_token(token: str) -> Dict[str, Any]:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = hmac.new(
            _signing_secret(), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_decode(supplied_signature), expected_signature):
            raise SignedTokenError("Invalid token signature")
        payload = json.loads(_decode(encoded).decode("utf-8"))
    except SignedTokenError:
        raise
    except Exception as exc:
        raise SignedTokenError("Malformed signed token") from exc

    if int(payload.get("exp", 0)) < int(time.time()):
        raise SignedTokenError("Signed token has expired")
    return payload
