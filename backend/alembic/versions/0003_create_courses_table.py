"""Create courses table

Revision ID: 0003_create_courses
Revises: 0002_create_refresh_sessions
Create Date: 2025-12-15

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0003_create_courses"
down_revision = "0002_create_refresh_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_courses_user_id", "courses", ["user_id"], unique=False)
    op.create_index("ix_courses_user_id_created_at", "courses", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_courses_user_id_created_at", table_name="courses")
    op.drop_index("ix_courses_user_id", table_name="courses")
    op.drop_table("courses")


