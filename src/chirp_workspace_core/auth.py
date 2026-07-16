"""Adapters for Chirp's existing application-owned authentication seam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from chirp.middleware.auth import AuthConfig, User

from .models import WorkspaceUser


class WorkspaceUserLoader(Protocol):
    """Smallest repository shape required by Chirp session authentication."""

    def load_user(self, user_id: str) -> Awaitable[WorkspaceUser | None]: ...


def _session_version(user: User) -> str | int | None:
    """Read Core's revocation counter without changing Chirp's user protocol."""

    value = getattr(user, "session_version", None)
    return value if isinstance(value, (str, int)) else None


def chirp_auth_config(
    loader: WorkspaceUserLoader,
    *,
    login_url: str | None = "/login",
    verify_token: Callable[[str], Awaitable[User | None]] | None = None,
) -> AuthConfig:
    """Build an ``AuthConfig`` using Core users and session invalidation.

    Workspace membership and permissions remain request/workspace scoped; the
    loaded user proves local identity only. Products resolve a principal for
    the active workspace before authorizing tenant data.
    """

    return AuthConfig(
        load_user=loader.load_user,
        verify_token=verify_token,
        session_version=_session_version,
        login_url=login_url,
    )
