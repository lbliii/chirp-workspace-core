"""One-shot pre-deploy migration command using Chirp's released public API."""

import asyncio
import os

from chirp.data import Database, migrate

from chirp_workspace_core.migrations import migration_directory


async def main() -> None:
    """Apply packaged Core migrations and fail the deployment on any error."""

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for Workspace Core migrations")
    database = Database(database_url)
    await database.connect()
    try:
        await migrate(database, migration_directory())
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
