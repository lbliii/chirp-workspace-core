"""Stable domain errors for Workspace Core.

The repository layer raises these errors instead of leaking database-driver
exceptions into route handlers.  Messages should name the affected surface
and identifier while tokens, password hashes, and other secrets stay out of
the error text.
"""


class WorkspaceCoreError(Exception):
    """Base class for expected Workspace Core failures."""


class ValidationError(WorkspaceCoreError):
    """Submitted data is malformed or violates a domain rule."""


class AuthenticationError(WorkspaceCoreError):
    """Local identity credentials or session state are invalid."""


class AuthorizationError(WorkspaceCoreError):
    """The current identity cannot perform an operation."""


class TenantMismatchError(AuthorizationError):
    """An identifier belongs to a workspace other than the authorized tenant."""


class NotFoundError(WorkspaceCoreError):
    """A tenant-scoped record does not exist or is not visible."""


class ConflictError(WorkspaceCoreError):
    """A concurrent or duplicate operation conflicts with durable state."""


class LastOwnerError(ConflictError):
    """The operation would leave a workspace without an owner."""


class DuplicateInvitationError(ConflictError):
    """An unconsumed invitation already exists for the workspace identity."""


class InvitationError(WorkspaceCoreError):
    """An invitation cannot be accepted."""


class InvitationExpiredError(InvitationError):
    """An invitation is past its expiration time."""


class InvitationRevokedError(InvitationError):
    """An invitation was explicitly revoked or replaced."""


class InvitationConsumedError(InvitationError):
    """An invitation token has already been used."""


class PasswordResetError(WorkspaceCoreError):
    """A password-reset token cannot be used."""


class PasswordResetExpiredError(PasswordResetError):
    """A password-reset token is past its expiration time."""


class PasswordResetRevokedError(PasswordResetError):
    """A password-reset token was explicitly revoked or replaced."""


class PasswordResetConsumedError(PasswordResetError):
    """A password-reset token has already been used."""


class DatabaseUnavailableError(WorkspaceCoreError):
    """The configured Workspace database cannot serve the operation."""


class SchemaMismatchError(WorkspaceCoreError):
    """The installed Workspace schema is incompatible with this package."""
