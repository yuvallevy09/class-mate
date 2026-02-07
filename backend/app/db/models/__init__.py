"""ORM models package.

Importing modules here is optional; Alembic loads models explicitly in env.py
to ensure metadata is populated for autogenerate.
"""

# Import models for convenient access in code.
# (Alembic does not rely on this file.)
from app.db.models.video_chapter import VideoChapter  # noqa: F401

