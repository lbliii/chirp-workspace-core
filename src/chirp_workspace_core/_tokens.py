"""Single-use token helpers.

Only token digests cross the repository boundary into durable storage.  The
plain token is returned once to the caller so an application can deliver it
manually or through an explicitly configured provider.
"""

import hashlib
import secrets


def issue_token() -> tuple[str, str]:
    """Return a high-entropy URL-safe token and its storage-safe digest."""
    token = secrets.token_urlsafe(32)
    return token, token_digest(token)


def token_digest(token: str) -> str:
    """Return the stable SHA-256 digest used for token lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Compare application setup tokens without a content timing oracle."""
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
