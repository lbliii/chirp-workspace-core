"""Packaged Workspace Core migration resources."""

from pathlib import Path

MIGRATIONS = Path(__file__).with_name("migrations")


def migration_directory() -> Path:
    """Return the installed Core migration directory for Chirp's runner."""

    if not MIGRATIONS.is_dir():
        msg = f"Workspace Core migrations are missing from the installed package: {MIGRATIONS}"
        raise RuntimeError(msg)
    return MIGRATIONS
