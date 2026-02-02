"""Add citations JSON to chat_messages

Revision ID: 0017_chat_msg_citations
Revises: 0016_video_asset_ai_summary
Create Date: 2026-02-02

Persist assistant citations separately from the rendered message content so the frontend
can render a structured Sources section reliably.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0017_chat_msg_citations"
down_revision = "0016_video_asset_ai_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "citations")

