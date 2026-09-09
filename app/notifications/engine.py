"""Персистентная очередь. Сетевой вызов всегда вне SQLite-транзакции."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.birthdays.service import age_in_year, occurrence_in_year
from app.db.models import Birthday, Delivery, DeliveryAttempt, Group, TelegramLink, User

MOSCOW = ZoneInfo("Europe/Moscow")
OFFSETS = (7, 1, 0)
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 900, 3600)
LEASE = timedelta(minutes=5)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Время должно содержать часовой пояс")
    return value.astimezone(UTC)


def scheduled_at(occurrence: date, days_before: int) -> datetime:
    return datetime.combine(occurrence - timedelta(days=days_before), time(9), MOSCOW)


def window_open(occurrence: date, days_before: int, now: datetime) -> bool:
    local = utc(now).astimezone(MOSCOW)
    due = scheduled_at(occurrence, days_before)
    return due <= local and due.date() == local.date()


@dataclass(frozen=True)
class Message:
    delivery_id: int
    attempt: int
    telegram_id: int
    text: str
    url: str
    snooze_callback: str | None = None


class Sender(Protocol):
    def send(self, message: Message) -> None:
        """Подтвержденный успех либо типизированная ошибка; transport timeout не более 30 секунд."""


class RetryableError(Exception):
    """Подтвержденный отказ без доставки: повтор безопасен."""

    def __init__(self, *, retry_after: int = 0):
        self.retry_after = max(0, retry_after)


class PermanentError(Exception):
    """Подтвержденная постоянная ошибка."""


class UnknownResultError(Exception):
    """Неизвестно, получил ли адресат сообщение. Автоматически не повторяем."""


def message_for(delivery, person, group, link, base_url):
    when = {7: "Через 7 дней", 1: "Завтра", 0: "Сегодня"}[delivery.days_before]
    age = age_in_year(person, delivery.occurrence_date.year)
    lines = [
        f"🎂 {when} день рождения: {person.name}",
        f"Дата: {delivery.occurrence_date:%d.%m.%Y}",
        f"Группа: {group.name}",
    ]
    if age is not None:
        lines.append(f"Возраст: {age}")
    return Message(
        delivery.id,
        delivery.attempts,
        link.telegram_id,
        "\n".join(lines),
        f"{base_url.rstrip('/')}/birthdays/{person.id}",
        f"evening:{delivery.id}",
    )


class NotificationEngine:
    delivery_model = Delivery
    attempt_model = DeliveryAttempt

    def __init__(
        self,
        db_engine,
        *,
        base_url: str,
        sender: Sender | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db_engine = db_engine
        self.base_url = base_url
        self.sender = sender
        self.clock = clock or (lambda: datetime.now(UTC))

    def session(self):
        return Session(self.db_engine.execution_options(sqlite_write=True))

    def enqueue(self) -> int:
        now = utc(self.clock())
        local = now.astimezone(MOSCOW)
        if local.time() < time(9):
            return 0
        inserted = 0
        with self.session() as db, db.begin():
            recipients = list(
                db.scalars(select(User.id).join(TelegramLink).where(User.is_active.is_(True)))
            )
            for person in db.scalars(select(Birthday).where(Birthday.is_active.is_(True))):
                for offset in OFFSETS:
                    target = local.date() + timedelta(days=offset)
                    if occurrence_in_year(person, target.year) != target:
                        continue
                    for user_id in recipients:
                        result = db.execute(
                            insert(Delivery)
                            .values(
                                birthday_id=person.id,
                                occurrence_date=target,
                                days_before=offset,
                                user_id=user_id,
                                next_attempt_at=scheduled_at(target, offset)
                                .astimezone(UTC)
                                .replace(tzinfo=None),
                            )
                            .on_conflict_do_nothing(
                                index_elements=[
                                    "birthday_id",
                                    "occurrence_date",
                                    "days_before",
                                    "user_id",
                                ]
                            )
                        )
                        inserted += result.rowcount
        return inserted

    @staticmethod
    def delivery_window_open(delivery, now):
        return window_open(delivery.occurrence_date, delivery.days_before, now)

    def build_message(self, delivery, person, group, link):
        return message_for(delivery, person, group, link, self.base_url)

    def maintain(self):
        now = utc(self.clock())
        with self.session() as db, db.begin():
            for delivery in db.scalars(
                select(self.delivery_model).where(
                    self.delivery_model.status.in_(("pending", "retry", "sending"))
                )
            ):
                if delivery.status == "sending":
                    if delivery.next_attempt_at <= now.replace(tzinfo=None):
                        delivery.status = "failed"
                        delivery.last_error = (
                            "Результат прерванной отправки неизвестен; повтор отключен"
                        )
                        db.add(
                            self.attempt_model(
                                delivery_id=delivery.id,
                                attempt_number=delivery.attempts,
                                outcome="failed",
                                error=delivery.last_error,
                                attempted_at=now.replace(tzinfo=None),
                            )
                        )
                    continue
                person = db.get(Birthday, delivery.birthday_id)
                user = db.get(User, delivery.user_id)
                link = db.get(TelegramLink, delivery.user_id)
                if not self.eligible(delivery, person, user, link, now):
                    delivery.status = "cancelled"
                    delivery.last_error = "Окно отправки закрыто или запись/получатель недоступны"

    @classmethod
    def eligible(cls, delivery, person, user, link, now):
        return bool(
            person
            and person.is_active
            and user
            and user.is_active
            and link
            and occurrence_in_year(person, delivery.occurrence_date.year)
            == delivery.occurrence_date
            and cls.delivery_window_open(delivery, now)
        )

    def claim(self) -> Message | None:
        now = utc(self.clock())
        with self.session() as db, db.begin():
            candidates = db.scalars(
                select(self.delivery_model)
                .where(
                    self.delivery_model.status.in_(("pending", "retry")),
                    self.delivery_model.next_attempt_at <= now.replace(tzinfo=None),
                )
                .order_by(self.delivery_model.next_attempt_at, self.delivery_model.id)
            )
            for delivery in candidates:
                person = db.get(Birthday, delivery.birthday_id)
                user = db.get(User, delivery.user_id)
                link = db.get(TelegramLink, delivery.user_id)
                if not self.eligible(delivery, person, user, link, now):
                    delivery.status = "cancelled"
                    delivery.last_error = "Окно отправки закрыто или запись/получатель недоступны"
                    continue
                if delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = "failed"
                    delivery.last_error = "Достигнут предел попыток"
                    continue
                delivery.status = "sending"
                delivery.attempts += 1
                delivery.next_attempt_at = (now + LEASE).replace(tzinfo=None)
                return self.build_message(delivery, person, db.get(Group, person.group_id), link)
        return None

    def finish(
        self, message: Message, *, outcome: str, error: str | None = None, retry_after: int = 0
    ):
        now = utc(self.clock())
        with self.session() as db, db.begin():
            delivery = db.get(self.delivery_model, message.delivery_id)
            if (
                not delivery
                or delivery.attempts != message.attempt
                or delivery.status not in ("sending", "cancelled")
            ):
                return
            cancelled = delivery.status == "cancelled"
            status = outcome
            if outcome == "retry":
                delay = max(BACKOFF_SECONDS[min(message.attempt - 1, 3)], retry_after)
                retry_at = now + timedelta(seconds=min(delay, 86_400))
                if message.attempt >= MAX_ATTEMPTS or not self.delivery_window_open(
                    delivery, retry_at
                ):
                    outcome, status = "failed", "failed"
                    error = "Исчерпаны попытки или окно повторной отправки"
                else:
                    delivery.next_attempt_at = retry_at.replace(tzinfo=None)
            # Архивирование в ходе запроса не отменяет уже доставленное сообщение.
            delivery.status = "cancelled" if cancelled and outcome != "sent" else status
            delivery.last_error = error
            if outcome == "sent":
                delivery.sent_at = now.replace(tzinfo=None)
            db.add(
                self.attempt_model(
                    delivery_id=delivery.id,
                    attempt_number=message.attempt,
                    outcome=outcome,
                    error=error,
                    attempted_at=now.replace(tzinfo=None),
                )
            )

    def dispatch_one(self) -> bool:
        sender = self.sender
        if sender is None:
            return False
        message = self.claim()
        if message is None:
            return False
        try:
            sender.send(message)
        except RetryableError as error:
            self.finish(
                message,
                outcome="retry",
                error="Временный отказ сервиса доставки",
                retry_after=error.retry_after,
            )
        except PermanentError:
            self.finish(message, outcome="failed", error="Постоянный отказ сервиса доставки")
        except Exception:
            # Не сохраняем exception text: он может содержать token, URL или персональные данные.
            self.finish(
                message, outcome="failed", error="Результат отправки неизвестен; повтор отключен"
            )
        else:
            self.finish(message, outcome="sent")
        return True

    def tick(self, *, batch_size: int = 50, should_stop: Callable[[], bool] = lambda: False) -> int:
        self.maintain()
        self.enqueue()
        count = 0
        for _ in range(batch_size):
            if should_stop() or not self.dispatch_one():
                break
            count += 1
        return count
