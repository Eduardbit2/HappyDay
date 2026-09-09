"""Личный однократный повтор исходного напоминания в 19:00 МСК."""

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

from sqlalchemy.dialects.sqlite import insert

from app.birthdays.service import occurrence_in_year
from app.db.models import Birthday, Delivery, EveningAttempt, EveningReminder, TelegramLink, User
from app.notifications.engine import MOSCOW, NotificationEngine, message_for, utc


def request_evening(db, user_id: int, delivery_id: int, now: datetime) -> str:
    local = utc(now).astimezone(MOSCOW)
    original = db.get(Delivery, delivery_id)
    user = db.get(User, user_id)
    if (
        not original
        or original.user_id != user_id
        or original.status != "sent"
        or not user
        or not user.is_active
        or not db.get(TelegramLink, user_id)
    ):
        raise ValueError("Это напоминание недоступно.")
    person = db.get(Birthday, original.birthday_id)
    if (
        not person
        or not person.is_active
        or occurrence_in_year(person, original.occurrence_date.year) != original.occurrence_date
        or original.occurrence_date - timedelta(days=original.days_before) != local.date()
    ):
        raise ValueError("Напоминание устарело. Откройте список ближайших дней рождения.")
    if local.time() >= time(19):
        raise ValueError("Вечерний повтор можно запросить до 19:00 МСК в день напоминания.")
    due = datetime.combine(local.date(), time(19), MOSCOW).astimezone(UTC).replace(tzinfo=None)
    result = db.execute(
        insert(EveningReminder)
        .values(
            source_delivery_id=original.id,
            birthday_id=original.birthday_id,
            user_id=user_id,
            occurrence_date=original.occurrence_date,
            days_before=original.days_before,
            due_at=due,
            next_attempt_at=due,
        )
        .on_conflict_do_nothing(index_elements=["source_delivery_id"])
    )
    return (
        "Напомню сегодня в 19:00 МСК."
        if result.rowcount
        else "Вечерний повтор для этого напоминания уже был запрошен."
    )


class EveningEngine(NotificationEngine):
    delivery_model = EveningReminder
    attempt_model = EveningAttempt

    def enqueue(self) -> int:
        # Создается только явным нажатием кнопки, а не ежедневным сканированием.
        return 0

    @staticmethod
    def delivery_window_open(delivery, now):
        # До due_at запись остается в очереди; claim отбирает только наступившее время.
        due = delivery.due_at.replace(tzinfo=UTC).astimezone(MOSCOW)
        return due.date() == utc(now).astimezone(MOSCOW).date()

    def build_message(self, delivery, person, group, link):
        message = message_for(delivery, person, group, link, self.base_url)
        return replace(
            message, text="⏰ Вечернее напоминание\n" + message.text, snooze_callback=None
        )
