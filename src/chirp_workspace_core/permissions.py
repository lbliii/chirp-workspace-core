"""Exact Workspace permission strings compiled from baseline membership roles."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from .errors import AuthorizationError
from .models import Role


class Permission(StrEnum):
    """Exact application permissions consumed by Chirp's existing policy seams."""

    WORKSPACE_READ = "workspace:read"
    CONTENT_READ = "content:read"
    CONTENT_WRITE = "content:write"
    ACTION_RUN = "actions:run"
    MEMBERS_INVITE = "members:invite"
    MEMBERS_MANAGE = "members:manage"
    MEMBER_ROLES_CHANGE = "members:roles:change"
    OWNERS_MANAGE = "owners:manage"
    WORKSPACE_DELETE = "workspace:delete"
    WORKSPACE_CONFIGURE = "workspace:configure"
    AUDIT_READ = "audit:read"


_VIEWER_PERMISSIONS = frozenset(
    {
        Permission.WORKSPACE_READ,
        Permission.CONTENT_READ,
    }
)
_MEMBER_PERMISSIONS = _VIEWER_PERMISSIONS | {
    Permission.CONTENT_WRITE,
    Permission.ACTION_RUN,
}
_ADMIN_PERMISSIONS = _MEMBER_PERMISSIONS | {
    Permission.MEMBERS_INVITE,
    Permission.MEMBERS_MANAGE,
    Permission.MEMBER_ROLES_CHANGE,
    Permission.WORKSPACE_CONFIGURE,
    Permission.AUDIT_READ,
}
_OWNER_PERMISSIONS = _ADMIN_PERMISSIONS | {
    Permission.OWNERS_MANAGE,
    Permission.WORKSPACE_DELETE,
}

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: _OWNER_PERMISSIONS,
        Role.ADMIN: _ADMIN_PERMISSIONS,
        Role.MEMBER: _MEMBER_PERMISSIONS,
        Role.VIEWER: _VIEWER_PERMISSIONS,
    }
)


def permissions_for_role(role: Role | str) -> frozenset[Permission]:
    """Return the immutable exact-permission set for ``role``."""

    try:
        resolved_role = Role(role)
    except ValueError:
        return frozenset()
    return ROLE_PERMISSIONS[resolved_role]


def has_permission(role: Role | str, permission: Permission | str) -> bool:
    """Return whether ``role`` grants an exact permission; unknown values fail closed."""

    try:
        resolved_permission = Permission(permission)
    except ValueError:
        return False
    return resolved_permission in permissions_for_role(role)


def require_permission(role: Role | str, permission: Permission | str) -> None:
    """Raise :class:`AuthorizationError` unless ``role`` grants ``permission``."""

    if has_permission(role, permission):
        return
    role_value = role.value if isinstance(role, Role) else role
    permission_value = permission.value if isinstance(permission, Permission) else permission
    raise AuthorizationError(
        f"Workspace role {role_value!r} does not grant permission {permission_value!r}."
    )
