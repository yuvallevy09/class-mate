from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure `import app...` works when running Alembic from the backend directory.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import user as _user_model  # noqa: F401,E402
from app.db.models import refresh_session as _refresh_session_model  # noqa: F401,E402
from app.db.models import course as _course_model  # noqa: F401,E402
from app.db.models import course_content as _course_content_model  # noqa: F401,E402
from app.db.models import chat_conversation as _chat_conversation_model  # noqa: F401,E402
from app.db.models import chat_message as _chat_message_model  # noqa: F401,E402
from app.db.models import video_asset as _video_asset_model  # noqa: F401,E402
from app.db.models import transcript_segment as _transcript_segment_model  # noqa: F401,E402
from app.db.models import document_page as _document_page_model  # noqa: F401,E402
from app.db.models import content_chunk as _content_chunk_model  # noqa: F401,E402

# Alembic Config object.
config = context.config

# Configure Python logging via alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support.
target_metadata = Base.metadata


def get_database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    # Inject runtime DB URL into Alembic config.
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())


