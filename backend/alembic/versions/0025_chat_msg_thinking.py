"""Add thinking text to chat_messages

Revision ID: 0025_chat_msg_thinking
Revises: 0024_course_ai_summary
Create Date: 2026-06-13

Persist the assistant's per-turn "thinking" explanation (why it routed/searched
the way it did) alongside the message so it survives a history reload, mirroring
how `citations` are stored separately from the rendered content.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0025_chat_msg_thinking"
down_revision = "0024_course_ai_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("thinking", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "thinking")
