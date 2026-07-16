# Security

Please report suspected vulnerabilities privately through GitHub's security
advisory flow for `lbliii/chirp-workspace-core`. Do not open a public issue with
tokens, password hashes, provider credentials, private endpoints, tenant data,
or reproduction data belonging to another user.

Workspace Core treats a workspace as the tenant boundary. Reports involving
cross-workspace access, invitation or reset replay, owner removal, stale session
authorization, activity or notification leakage, replay after membership
revocation, unsafe resource URLs, migration integrity, or secret exposure are
security issues. Product activity is not the security audit log, and metadata
must never contain tokens, passwords, credentials, cookies, or provider output
that has not been classified safe for the intended workspace recipients.
