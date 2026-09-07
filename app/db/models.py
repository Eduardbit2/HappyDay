"""Доменная схема. Все DateTime хранятся как UTC без смещения."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SYSTEM_GROUP_ID = 1


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="name"),
        CheckConstraint("length(login) BETWEEN 1 AND 100", name="login"),
        CheckConstraint("length(password_hash) > 0", name="password_hash"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(10), server_default="member")
    is_active: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="active"), server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 100", name="name"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(
            "(id = 1 AND is_system = 1 AND name = 'Без группы') OR (id <> 1 AND is_system = 0)",
            name="system_identity",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(100), server_default="🏷")
    color: Mapped[str] = mapped_column(String(7), server_default="#8A8178")
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    is_system: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="system"), server_default="0"
    )


class Birthday(Base):
    __tablename__ = "birthdays"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="name"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
        CheckConstraint("year IS NULL OR year BETWEEN 1 AND 9999", name="year"),
        CheckConstraint(
            "day BETWEEN 1 AND CASE WHEN month = 2 THEN "
            "CASE WHEN year IS NULL OR (year % 4 = 0 AND (year % 100 <> 0 OR year % 400 = 0)) "
            "THEN 29 ELSE 28 END WHEN month IN (4, 6, 9, 11) THEN 30 ELSE 31 END",
            name="calendar_date",
        ),
        Index("ix_birthdays_active_date", "is_active", "month", "day"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    day: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="RESTRICT"), server_default="1", index=True
    )
    note: Mapped[str] = mapped_column(Text, server_default=text("''"))
    is_active: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="active"), server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    __table_args__ = (CheckConstraint("telegram_id > 0", name="private_user"),)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint(
            "birthday_id",
            "occurrence_date",
            "days_before",
            "user_id",
            name="uq_delivery_event_recipient",
        ),
        CheckConstraint("days_before IN (0, 1, 7)", name="days_before"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'retry', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("status <> 'sent' OR sent_at IS NOT NULL", name="sent_at"),
        Index("ix_deliveries_due", "status", "next_attempt_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    birthday_id: Mapped[int] = mapped_column(ForeignKey("birthdays.id", ondelete="RESTRICT"))
    occurrence_date: Mapped[date] = mapped_column(Date)
    days_before: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class DeliveryAttempt(Base):
    """История результатов отдельных попыток доставки."""

    __tablename__ = "delivery_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="attempt_number"),
        CheckConstraint("outcome IN ('sent', 'retry', 'failed')", name="outcome"),
    )
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(10))
    error: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
