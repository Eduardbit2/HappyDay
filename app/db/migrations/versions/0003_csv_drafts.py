"""Одноразовые предпросмотры CSV, привязанные к серверной сессии."""

import sqlalchemy as sa
from alembic import op

revision = "0003_csv_drafts"
down_revision = "0002_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "csv_drafts",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_hash"],
            ["auth_sessions.token_hash"],
            name=op.f("fk_csv_drafts_session_hash_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_csv_drafts")),
        sa.UniqueConstraint("session_hash", name=op.f("uq_csv_drafts_session_hash")),
    )
    op.create_index(op.f("ix_csv_drafts_expires_at"), "csv_drafts", ["expires_at"])


def downgrade():
    # Теряются только незавершенные предпросмотры; семейные данные не меняются.
    op.drop_table("csv_drafts")
