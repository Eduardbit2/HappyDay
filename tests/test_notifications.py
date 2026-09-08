import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_db import database as database

from app.birthdays.service import archive_birthday, create_birthday
from app.db.models import Birthday, Delivery, DeliveryAttempt, Group, TelegramLink, User
from app.notifications.engine import (
    NotificationEngine,
    PermanentError,
    RetryableError,
    UnknownResultError,
    window_open,
)
from app.scheduler.runner import run_scheduler


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Sender:
    def __init__(self):
        self.messages = []
        self.error = None

    def send(self, message):
        self.messages.append(message)
        if self.error:
            raise self.error


@pytest.fixture
def queue(database):
    db_engine, _ = database
    clock = Clock(datetime(2026, 1, 1, 6, tzinfo=UTC))
    sender = Sender()
    with Session(db_engine) as db, db.begin():
        user = User(name="Алёна 🎂", login="test", password_hash="not-a-real-password")
        db.add(user)
        db.flush()
        db.add(TelegramLink(user_id=user.id, telegram_id=12345))
        create_birthday(db, name="Лёля Ёжик", day=1, month=1, year=2000)
    engine = NotificationEngine(
        db_engine, base_url="https://example.test", sender=sender, clock=clock
    )
    return engine, clock, sender


def rows(engine):
    with Session(engine.db_engine) as db:
        return list(db.scalars(select(Delivery).order_by(Delivery.id)))


def test_nine_moscow_and_same_day_catchup(queue):
    engine, clock, sender = queue
    clock.value -= timedelta(seconds=1)
    assert engine.tick() == 0
    assert rows(engine) == []
    clock.value += timedelta(seconds=1)
    assert engine.tick() == 1
    assert rows(engine)[0].status == "sent"
    assert "Лёля Ёжик" in sender.messages[0].text
    assert "Возраст: 26" in sender.messages[0].text
    assert sender.messages[0].url == "https://example.test/birthdays/1"
    assert engine.tick() == 0
    restarted = NotificationEngine(
        engine.db_engine, base_url="https://example.test", sender=sender, clock=clock
    )
    assert restarted.tick() == 0
    assert len(sender.messages) == 1


def test_offsets_year_boundary_and_unknown_age(queue):
    engine, clock, sender = queue
    clock.value = datetime(2025, 12, 25, 19, tzinfo=UTC)  # 22:00 Moscow, catch-up.
    assert engine.tick() == 1
    assert rows(engine)[0].days_before == 7
    assert rows(engine)[0].occurrence_date == date(2026, 1, 1)
    clock.value = datetime(2025, 12, 31, 6, tzinfo=UTC)
    assert engine.tick() == 1
    clock.value = datetime(2026, 1, 1, 6, tzinfo=UTC)
    assert engine.tick() == 1
    assert {row.days_before for row in rows(engine)} == {0, 1, 7}
    with Session(engine.db_engine) as db, db.begin():
        db.get(Birthday, 1).year = None
        create_birthday(db, name="Без года", day=8, month=1)
    engine.tick()
    assert "Возраст" not in sender.messages[-1].text


@pytest.mark.parametrize("year,event_day", [(2026, 28), (2028, 29)])
def test_leap_day(queue, year, event_day):
    engine, clock, _ = queue
    with Session(engine.db_engine) as db, db.begin():
        person = db.get(Birthday, 1)
        person.day, person.month, person.year = 29, 2, None
    clock.value = datetime(year, 2, event_day, 6, tzinfo=UTC)
    assert engine.tick() == 1
    assert rows(engine)[0].occurrence_date == date(year, 2, event_day)


def test_only_active_linked_recipients_and_birthdays(queue):
    engine, _clock, sender = queue
    with Session(engine.db_engine) as db, db.begin():
        for login, active, linked in (
            ("unlinked", True, False),
            ("inactive", False, True),
            ("second", True, True),
        ):
            user = User(name=login, login=login, password_hash="test", is_active=active)
            db.add(user)
            db.flush()
            if linked:
                db.add(TelegramLink(user_id=user.id, telegram_id=12345 + user.id))
        person = create_birthday(db, name="Архив", day=1, month=1)
        person.is_active = False
    assert engine.tick() == 2
    assert len(sender.messages) == 2


@pytest.mark.parametrize("change", ["archive", "disable", "unlink", "date"])
def test_revalidate_before_attempt(queue, change):
    engine, _clock, sender = queue
    engine.enqueue()
    with Session(engine.db_engine) as db, db.begin():
        if change == "archive":
            archive_birthday(db, 1)
        elif change == "disable":
            db.get(User, 1).is_active = False
        elif change == "unlink":
            db.delete(db.get(TelegramLink, 1))
        else:
            db.get(Birthday, 1).day = 2
    assert not engine.dispatch_one()
    assert not sender.messages
    assert rows(engine)[0].status == "cancelled"


def test_retry_backoff_limit_and_attempt_history(queue):
    engine, clock, sender = queue
    sender.error = RetryableError()
    engine.tick()
    assert rows(engine)[0].status == "retry"
    assert engine.tick() == 0
    for seconds in (60, 300, 900, 3600):
        clock.value += timedelta(seconds=seconds)
        assert engine.tick() == 1
    assert rows(engine)[0].status == "failed"
    assert rows(engine)[0].attempts == 5
    assert engine.tick() == 0
    with Session(engine.db_engine) as db:
        assert [
            a.outcome
            for a in db.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        ] == ["retry"] * 4 + ["failed"]


def test_retry_after_and_success(queue):
    engine, clock, sender = queue
    sender.error = RetryableError(retry_after=120)
    engine.tick()
    clock.value += timedelta(seconds=60)
    assert engine.tick() == 0
    clock.value += timedelta(seconds=60)
    sender.error = None
    assert engine.tick() == 1
    row = rows(engine)[0]
    assert row.status == "sent" and row.attempts == 2 and row.last_error is None


@pytest.mark.parametrize(
    "error",
    [
        PermanentError("secret-token"),
        UnknownResultError("secret-token"),
        TimeoutError("secret-token"),
    ],
)
def test_permanent_or_uncertain_failure_no_retry_or_secret(queue, error):
    engine, clock, sender = queue
    sender.error = error
    assert engine.tick() == 1
    assert rows(engine)[0].status == "failed"
    assert "secret-token" not in rows(engine)[0].last_error
    clock.value += timedelta(hours=1)
    assert engine.tick() == 0


def test_crash_after_claim_not_blindly_resent(queue):
    engine, clock, sender = queue
    engine.enqueue()
    claim = engine.claim()
    assert claim is not None
    assert not engine.dispatch_one()
    clock.value += timedelta(minutes=6)
    engine.tick()
    assert rows(engine)[0].status == "failed"
    assert not sender.messages
    with Session(engine.db_engine) as db:
        assert db.scalar(select(DeliveryAttempt)).outcome == "failed"


def test_window_ends_at_moscow_midnight(queue):
    engine, clock, sender = queue
    engine.enqueue()
    clock.value = datetime(2026, 1, 1, 21, tzinfo=UTC)
    assert engine.tick() == 0
    assert rows(engine)[0].status == "cancelled"
    assert not sender.messages
    assert not window_open(date(2026, 1, 1), 0, clock.value)
    with pytest.raises(ValueError):
        window_open(date(2026, 1, 1), 0, datetime(2026, 1, 1))


def test_retry_does_not_cross_midnight(queue):
    engine, clock, sender = queue
    clock.value = datetime(2026, 1, 1, 20, 59, 30, tzinfo=UTC)
    sender.error = RetryableError()
    engine.tick()
    assert rows(engine)[0].status == "failed"


def test_concurrent_enqueue_and_claim_are_unique(queue):
    engine, _clock, _sender = queue
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _: engine.enqueue(), range(2))) == 1
        claims = list(executor.map(lambda _: engine.claim(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert rows(engine)[0].attempts == 1


def test_sender_runs_outside_database_lock(queue):
    engine, _clock, _sender = queue

    class WritingSender:
        def send(self, message):
            with Session(engine.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
                db.get(Group, 1).icon = "🎂"
                archive_birthday(db, 1)

    engine.sender = WritingSender()
    engine.tick()
    # The request succeeded while the birthday was being archived: preserve the true outcome.
    assert rows(engine)[0].status == "sent"


def test_missing_transport_does_not_fake_success(queue):
    engine, _clock, sender = queue
    engine.sender = None
    assert engine.tick() == 0
    assert rows(engine)[0].status == "pending"
    assert rows(engine)[0].attempts == 0
    assert not sender.messages


def test_scheduler_survives_failure_and_stops():
    async def scenario():
        stop = asyncio.Event()

        class Engine:
            calls = 0

            def tick(self, *, should_stop):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("do-not-log-secret")
                stop.set()

        engine = Engine()
        await asyncio.wait_for(run_scheduler(engine, stop, interval=0.001), timeout=5)
        assert engine.calls == 2

    asyncio.run(scenario())


def test_sender_snapshot_survives_runtime_disconnect(queue, monkeypatch):
    engine, _clock, sender = queue
    engine.enqueue()
    original_claim = engine.claim

    def claim_and_disconnect():
        message = original_claim()
        engine.sender = None
        return message

    monkeypatch.setattr(engine, "claim", claim_and_disconnect)
    assert engine.dispatch_one()
    assert len(sender.messages) == 1
    assert rows(engine)[0].status == "sent"
