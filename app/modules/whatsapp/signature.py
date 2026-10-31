"""HMAC-SHA256 signature of webhook bodies sent by the WhatsApp gateway."""

import hashlib
import hmac

_PREFIX = "sha256="


def sign_body(secret: str, body: bytes) -> str:
    """Return ``sha256=<lowercase hex>``, the value of the ``X-Signature`` header."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of an ``X-Signature`` header; any malformed value is False.

    Strict on purpose: the digest must be lowercase hex, exactly as ``sign_body`` produces it.
    A header that is not ``str`` or a body that is not ``bytes`` is False, never an exception.
    """
    if not isinstance(header, str) or not isinstance(body, bytes):
        return False
    if not header or not header.startswith(_PREFIX):
        return False
    expected = sign_body(secret, body).encode("ascii")
    try:
        received = header.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected, received)
