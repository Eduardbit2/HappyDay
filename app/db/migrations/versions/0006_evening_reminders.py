"""Вечерние повторы; исходные доставки и история сохраняются."""

import sqlalchemy as sa
from alembic import op

revision = "0006_evening_reminders"
down_revision = "0005_bot_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evening_reminders",
        sa.Column("source_delivery_id", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("birthday_id", sa.Integer(), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("days_before", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL", name=op.f("ck_evening_reminders_sent_at")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'retry', 'failed', 'cancelled')",
            name=op.f("ck_evening_reminders_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_evening_reminders_attempts")),
        sa.CheckConstraint(
            "days_before IN (0, 1, 7)", name=op.f("ck_evening_reminders_days_before")
        ),
        sa.ForeignKeyConstraint(
            ["birthday_id"],
            ["birthdays.id"],
            name=op.f("fk_evening_reminders_birthday_id_birthdays"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_evening_reminders_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evening_reminders")),
        sa.UniqueConstraint(
            "source_delivery_id", name=op.f("uq_evening_reminders_source_delivery_id")
        ),
        sa.ForeignKeyConstraint(
            ["source_delivery_id"],
            ["deliveries.id"],
            name=op.f("fk_evening_reminders_source_delivery_id_deliveries"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_evening_reminders_due", "evening_reminders", ["status", "next_attempt_at"], unique=False
    )

    op.create_table(
        "evening_attempts",
        sa.Column("delivery_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('sent', 'retry', 'failed')", name=op.f("ck_evening_attempts_outcome")
        ),
        sa.CheckConstraint("attempt_number > 0", name=op.f("ck_evening_attempts_attempt_number")),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["evening_reminders.id"],
            name=op.f("fk_evening_attempts_delivery_id_evening_reminders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("delivery_id", "attempt_number", name=op.f("pk_evening_attempts")),
    )


def downgrade():
    # Теряются только вечерние запросы и их история.
    op.drop_table("evening_attempts")
    op.drop_index("ix_evening_reminders_due", table_name="evening_reminders")
    op.drop_table("evening_reminders")
