from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.user import User


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create a dev user in the database.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            res = await session.execute(select(User).where(User.email == args.email))
            existing = res.scalar_one_or_none()
            if existing is not None:
                print(f"User already exists: id={existing.id} email={existing.email}")
                return

            user = User(email=args.email, hashed_password=hash_password(args.password))
            session.add(user)
            await session.commit()
            await session.refresh(user)
            print(f"Created user: id={user.id} email={user.email}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())


