"""Черновики Telegram, удаляемые при отключении привязки."""

import sqlalchemy as sa
from alembic import op

revision = "0005_bot_drafts"
down_revision = "0004_telegram_pairing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_drafts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_links.user_id"],
            name=op.f("fk_bot_drafts_user_id_telegram_links"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_bot_drafts")),
    )


def downgrade():
    # Удаляются только незавершенные диалоги; готовые записи и привязки сохраняются.
    op.drop_table("bot_drafts")
