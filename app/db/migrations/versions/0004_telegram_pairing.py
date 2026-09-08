"""Подтверждаемые в web привязки и курсор Telegram polling."""

import sqlalchemy as sa
from alembic import op

revision = "0004_telegram_pairing"
down_revision = "0003_csv_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_pair_requests",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_pair_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_hash"],
            ["auth_sessions.token_hash"],
            name=op.f("fk_telegram_pair_requests_session_hash_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_telegram_pair_requests")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_telegram_pair_requests_token_hash")),
    )
    op.create_table(
        "bot_cursors",
        sa.Column("bot_id", sa.String(20), nullable=False),
        sa.Column("next_update_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", name=op.f("pk_bot_cursors")),
    )


def downgrade():
    # Дни рождения, аккаунты и подтвержденные привязки остаются.
    # Курсор теряется: перед повторным включением polling учитывайте повтор старых updates.
    op.drop_table("bot_cursors")
    op.drop_table("telegram_pair_requests")
