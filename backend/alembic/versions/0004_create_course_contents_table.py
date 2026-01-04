"""Create course contents table

Revision ID: 0004_create_course_contents
Revises: 0003_create_courses
Create Date: 2025-12-15

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0004_create_course_contents"
down_revision = "0003_create_courses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_contents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("file_key", sa.String(length=1024), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_course_contents_course_id", "course_contents", ["course_id"], unique=False)
    op.create_index(
        "ix_course_contents_course_id_category_created_at",
        "course_contents",
        ["course_id", "category", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_course_contents_course_id_category_created_at", table_name="course_contents")
    op.drop_index("ix_course_contents_course_id", table_name="course_contents")
    op.drop_table("course_contents")




