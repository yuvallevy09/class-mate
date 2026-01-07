"""Add CHECK constraint for course_contents.category

Revision ID: 0012_cc_category_check
Revises: 0011_rename_categories
Create Date: 2026-01-07

Enforces canonical categories at the DB level to prevent typos/drift.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0012_cc_category_check"
down_revision = "0011_rename_categories"
branch_labels = None
depends_on = None


_ALLOWED = ("overview", "media", "notes", "assignments", "exams", "additional_resources")
_NAME = "ck_course_contents_category"


def upgrade() -> None:
    # Guard: fail fast with a clear error if any existing rows contain unexpected categories.
    allowed_sql = ", ".join([f"'{c}'" for c in _ALLOWED])
    op.execute(
        sa.text(
            f"""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM course_contents
    WHERE category NOT IN ({allowed_sql})
  ) THEN
    RAISE EXCEPTION 'course_contents.category has unexpected values; please normalize before adding CHECK constraint';
  END IF;
END $$;
"""
        )
    )

    op.create_check_constraint(
        _NAME,
        "course_contents",
        f"category IN ({allowed_sql})",
    )


def downgrade() -> None:
    op.drop_constraint(_NAME, "course_contents", type_="check")


