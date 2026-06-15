"""Anchor chat conversations to a video lecture

Revision ID: 0026_convo_video_asset
Revises: 0025_chat_msg_thinking
Create Date: 2026-06-15

Add a nullable `video_asset_id` to `chat_conversations` so conversations started
from the video player are tagged with their lecture (NULL = course-level chat).
A composite index on (course_id, video_asset_id, last_message_at) backs the
video-chat widget's "conversations for this lecture, newest first" lookup.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# IMPORTANT: alembic_version.version_num is VARCHAR(32) by default, so keep this <= 32 chars.
revision = "0026_convo_video_asset"
down_revision = "0025_chat_msg_thinking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_conversations",
        sa.Column("video_asset_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_conversations_video_asset_id",
        "chat_conversations",
        "video_assets",
        ["video_asset_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_chat_conversations_course_video_last_msg",
        "chat_conversations",
        ["course_id", "video_asset_id", "last_message_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_conversations_course_video_last_msg", table_name="chat_conversations")
    op.drop_constraint(
        "fk_chat_conversations_video_asset_id", "chat_conversations", type_="foreignkey"
    )
    op.drop_column("chat_conversations", "video_asset_id")
