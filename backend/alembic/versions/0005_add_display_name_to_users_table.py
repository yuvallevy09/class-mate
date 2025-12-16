"""Add display_name to users table

Revision ID: 0005_add_display_name_to_users
Revises: 0004_create_course_contents
Create Date: 2025-12-16

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_add_display_name_to_users"
down_revision = "0004_create_course_contents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")


