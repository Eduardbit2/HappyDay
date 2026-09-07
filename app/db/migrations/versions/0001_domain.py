"""Начальная доменная схема, системная группа и защитные триггеры."""

import sqlalchemy as sa
from alembic import op

revision = "0001_domain"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("icon", sa.String(length=100), server_default="🏷", nullable=False),
        sa.Column("color", sa.String(length=7), server_default="#8A8178", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "is_system",
            sa.Boolean(create_constraint=True, name="system"),
            server_default="0",
            nullable=False,
        ),
        sa.CheckConstraint(
            "(id = 1 AND is_system = 1 AND name = 'Без группы') OR (id <> 1 AND is_system = 0)",
            name=op.f("ck_groups_system_identity"),
        ),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 100", name=op.f("ck_groups_name")),
        sa.CheckConstraint("sort_order >= 0", name=op.f("ck_groups_sort_order")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=10), server_default="member", nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(create_constraint=True, name="active"),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("role IN ('admin', 'member')", name=op.f("ck_users_role")),
        sa.CheckConstraint("length(login) BETWEEN 1 AND 100", name=op.f("ck_users_login")),
        sa.CheckConstraint("length(password_hash) > 0", name=op.f("ck_users_password_hash")),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name=op.f("ck_users_name")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login", name=op.f("uq_users_login")),
    )
    op.create_table(
        "birthdays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(create_constraint=True, name="active"),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "day BETWEEN 1 AND CASE WHEN month = 2 THEN "
            "CASE WHEN year IS NULL OR (year % 4 = 0 AND (year % 100 <> 0 OR year % 400 = 0)) "
            "THEN 29 ELSE 28 END WHEN month IN (4, 6, 9, 11) THEN 30 ELSE 31 END",
            name=op.f("ck_birthdays_calendar_date"),
        ),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name=op.f("ck_birthdays_name")),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name=op.f("ck_birthdays_month")),
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1 AND 9999", name=op.f("ck_birthdays_year")
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_birthdays_group_id_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_birthdays")),
    )
    op.create_index(
        "ix_birthdays_active_date", "birthdays", ["is_active", "month", "day"], unique=False
    )
    op.create_index(op.f("ix_birthdays_group_id"), "birthdays", ["group_id"], unique=False)
    op.create_table(
        "telegram_links",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("telegram_id > 0", name=op.f("ck_telegram_links_private_user")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_telegram_links")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_telegram_links_telegram_id")),
    )
    op.create_table(
        "deliveries",
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
            "status <> 'sent' OR sent_at IS NOT NULL", name=op.f("ck_deliveries_sent_at")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'retry', 'failed', 'cancelled')",
            name=op.f("ck_deliveries_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_deliveries_attempts")),
        sa.CheckConstraint("days_before IN (0, 1, 7)", name=op.f("ck_deliveries_days_before")),
        sa.ForeignKeyConstraint(
            ["birthday_id"],
            ["birthdays.id"],
            name=op.f("fk_deliveries_birthday_id_birthdays"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_deliveries_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
        sa.UniqueConstraint(
            "birthday_id",
            "occurrence_date",
            "days_before",
            "user_id",
            name="uq_delivery_event_recipient",
        ),
    )
    op.create_index("ix_deliveries_due", "deliveries", ["status", "next_attempt_at"], unique=False)

    op.create_table(
        "delivery_attempts",
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
            "outcome IN ('sent', 'retry', 'failed')", name=op.f("ck_delivery_attempts_outcome")
        ),
        sa.CheckConstraint("attempt_number > 0", name=op.f("ck_delivery_attempts_attempt_number")),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["deliveries.id"],
            name=op.f("fk_delivery_attempts_delivery_id_deliveries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("delivery_id", "attempt_number", name=op.f("pk_delivery_attempts")),
    )
    op.execute("INSERT INTO groups (id, name, is_system) VALUES (1, 'Без группы', 1)")
    op.execute("""CREATE TRIGGER protect_system_group_delete BEFORE DELETE ON groups
        WHEN OLD.id = 1 BEGIN SELECT RAISE(ABORT, 'system group cannot be deleted'); END""")
    op.execute("""CREATE TRIGGER protect_system_group_update BEFORE UPDATE ON groups
        WHEN OLD.id = 1 AND (NEW.id <> 1 OR NEW.name <> 'Без группы' OR NEW.is_system <> 1)
        BEGIN SELECT RAISE(ABORT, 'system group identity cannot be changed'); END""")
    for action in ("INSERT", "UPDATE"):
        op.execute(f"""CREATE TRIGGER birthday_future_year_{action.lower()}
            BEFORE {action} ON birthdays
            WHEN NEW.year > CAST(strftime('%Y', 'now', '+3 hours') AS INTEGER)
            BEGIN SELECT RAISE(ABORT, 'birth year cannot be in the future'); END""")


def downgrade():
    op.drop_table("delivery_attempts")
    # Только для пустых/тестовых БД: downgrade удаляет доменные данные.
    op.execute("DROP TRIGGER birthday_future_year_insert")
    op.execute("DROP TRIGGER birthday_future_year_update")
    op.execute("DROP TRIGGER protect_system_group_update")
    op.execute("DROP TRIGGER protect_system_group_delete")
    op.drop_index("ix_deliveries_due", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_table("telegram_links")
    op.drop_index(op.f("ix_birthdays_group_id"), table_name="birthdays")
    op.drop_index("ix_birthdays_active_date", table_name="birthdays")
    op.drop_table("birthdays")
    op.drop_table("users")
    op.drop_table("groups")
