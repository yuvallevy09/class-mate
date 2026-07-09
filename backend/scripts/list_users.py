from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as a plain file (`python scripts/list_users.py`) by putting
# the backend root on sys.path so `import app...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import get_settings
from app.db.models.course import Course  # noqa: F401 - ensure mapper registry has Course for User.courses
from app.db.models.user import User


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            res = await session.execute(select(User).order_by(User.id))
            users = res.scalars().all()
            if not users:
                print("No users in the database.")
                return
            print(f"Found {len(users)} user(s):\n")
            for u in users:
                name = f" (display_name={u.display_name!r})" if u.display_name else ""
                print(f"  id={u.id}  email={u.email!r}{name}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
