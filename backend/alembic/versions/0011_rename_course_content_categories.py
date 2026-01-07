"""Rename course_contents categories to new canonical values

Revision ID: 0011_rename_categories
Revises: 0010_video_content_fk
Create Date: 2026-01-07

Renames:
- past_assignments -> assignments
- past_exams -> exams
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0011_rename_categories"
down_revision = "0010_video_content_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE course_contents SET category = 'assignments' WHERE category = 'past_assignments'"))
    op.execute(sa.text("UPDATE course_contents SET category = 'exams' WHERE category = 'past_exams'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE course_contents SET category = 'past_assignments' WHERE category = 'assignments'"))
    op.execute(sa.text("UPDATE course_contents SET category = 'past_exams' WHERE category = 'exams'"))


