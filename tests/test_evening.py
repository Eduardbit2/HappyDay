import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select
from test_auth import auth_app as auth_app
from test_bot_wizard import callback_update
from test_db import database as database
from test_notifications import Sender
from test_notifications import queue as queue
from test_telegram import TOKEN, confirm, handle, issue, update
from test_telegram import connected_app as connected_app

from app.birthdays.service import archive_birthday, create_birthday, delete_birthday
from app.bot.api import TelegramAPI
from app.bot.linking import unlink
from app.bot.polling import BotRuntime
from app.db.migrations import migration_config, upgrade_database
from app.db.models import (
    Birthday,
    BotDraft,
    Delivery,
    DeliveryAttempt,
    EveningAttempt,
    EveningReminder,
    User,
)
from app.notifications.engine import NotificationEngine, RetryableError, UnknownResultError
from app.notifications.evening import EveningEngine, request_evening
from app.scheduler.runner import run_scheduler


def request(engine, clock, *, user_id=1, delivery_id=1):
    with engine.session() as db, db.begin():
        return request_evening(db, user_id, delivery_id, clock())


def get_evening(engine):
    with engine.session() as db:
        return db.scalar(select(EveningReminder))


@pytest.fixture
def evening(queue):
    original, clock, sender = queue
    assert original.tick() == 1
    engine = EveningEngine(
        original.db_engine,
        base_url=original.base_url,
        sender=sender,
        clock=clock,
    )
    return original, engine, clock, sender


def test_schedule_once_at_19_and_restart_preserves_original(evening):
    original, engine, clock, sender = evening
    assert sender.messages[0].snooze_callback == "evening:1"
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: request(engine, clock), range(2)))
    assert sum("Напомню сегодня" in answer for answer in answers) == 1
    assert get_evening(engine).due_at == datetime(2026, 1, 1, 16)
    clock.value = datetime(2026, 1, 1, 15, 59, 59, tzinfo=UTC)
    assert engine.tick() == 0
    assert get_evening(engine).status == "pending"
    restarted = EveningEngine(
        engine.db_engine,
        base_url=engine.base_url,
        sender=sender,
        clock=clock,
    )
    clock.value += timedelta(seconds=1)
    assert restarted.tick() == 1
    assert restarted.tick() == 0
    assert get_evening(engine).status == "sent"
    assert len(sender.messages) == 2
    assert "Вечернее напоминание" in sender.messages[-1].text
    assert "Сегодня день рождения: Лёля Ёжик" in sender.messages[-1].text
    assert sender.messages[-1].snooze_callback is None
    with engine.session() as db:
        assert db.get(Delivery, 1).attempts == 1
        assert db.get(DeliveryAttempt, (1, 1)).outcome == "sent"
        assert db.get(EveningAttempt, (1, 1)).outcome == "sent"


@pytest.mark.parametrize("hour,day", [(16, 1), (6, 2)])
def test_late_and_stale_requests_rejected(evening, hour, day):
    _, engine, clock, _ = evening
    clock.value = datetime(2026, 1, day, hour, tzinfo=UTC)
    with pytest.raises(ValueError):
        request(engine, clock)
    assert get_evening(engine) is None


@pytest.mark.parametrize(
    "change", ["other_user", "unsent", "archive", "date", "disabled", "unlink"]
)
def test_request_revalidates_owner_and_event(evening, change):
    _, engine, clock, _ = evening
    with engine.session() as db, db.begin():
        if change == "unsent":
            db.get(Delivery, 1).status = "failed"
        elif change == "archive":
            archive_birthday(db, 1)
        elif change == "date":
            db.get(Birthday, 1).day = 2
        elif change == "disabled":
            db.get(User, 1).is_active = False
        elif change == "unlink":
            unlink(db, 1)
    with pytest.raises(ValueError):
        request(engine, clock, user_id=2 if change == "other_user" else 1)
    assert get_evening(engine) is None


@pytest.mark.parametrize("change", ["archive", "date", "disabled", "unlink", "next_day"])
def test_pending_cancellation(evening, change):
    _, engine, clock, sender = evening
    request(engine, clock)
    with engine.session() as db, db.begin():
        if change == "archive":
            archive_birthday(db, 1)
        elif change == "date":
            create_birthday(db, birthday_id=1, name="Лёля Ёжик", day=2, month=1)
        elif change == "disabled":
            db.get(User, 1).is_active = False
        elif change == "unlink":
            unlink(db, 1)
    clock.value = datetime(2026, 1, 2 if change == "next_day" else 1, 16, tzinfo=UTC)
    assert engine.tick() == 0
    assert get_evening(engine).status == "cancelled"
    assert len(sender.messages) == 1


def test_evening_retry_and_unknown_outcome(evening):
    _, engine, clock, sender = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
    sender.error = RetryableError(retry_after=120)
    assert engine.tick() == 1
    assert get_evening(engine).status == "retry"
    clock.value += timedelta(seconds=119)
    assert engine.tick() == 0
    clock.value += timedelta(seconds=1)
    sender.error = UnknownResultError()
    assert engine.tick() == 1
    assert get_evening(engine).status == "failed"
    clock.value += timedelta(minutes=10)
    assert engine.tick() == 0
    with engine.session() as db:
        attempts = list(db.scalars(select(EveningAttempt).order_by(EveningAttempt.attempt_number)))
        assert [a.outcome for a in attempts] == ["retry", "failed"]


def test_interrupted_send_not_repeated(evening):
    _, engine, clock, _ = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
    assert engine.claim() is not None
    clock.value += timedelta(minutes=6)
    assert engine.tick() == 0
    assert get_evening(engine).status == "failed"


def test_retry_cannot_cross_midnight(evening):
    _, engine, clock, sender = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 20, 59, 30, tzinfo=UTC)
    sender.error = RetryableError()
    assert engine.tick() == 1
    assert get_evening(engine).status == "failed"


def test_archive_delete_cascades_evening_history(evening):
    _, engine, clock, _ = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
    engine.tick()
    with engine.session() as db, db.begin():
        archive_birthday(db, 1)
        delete_birthday(db, 1)
    with engine.session() as db:
        for model in (Birthday, Delivery, DeliveryAttempt, EveningReminder, EveningAttempt):
            assert db.scalar(select(model)) is None


def test_scheduler_connects_evening_sender(evening):
    original, engine, clock, sender = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
    engine.sender = None

    async def run():
        stop = asyncio.Event()

        class StopSender:
            def send(self, message):
                sender.send(message)
                stop.set()

        original.sender = StopSender()
        await asyncio.wait_for(
            run_scheduler(original, stop, evening_engine=engine, interval=0.01), 2
        )

    asyncio.run(run())
    assert get_evening(engine).status == "sent"


def test_callback_replay_draft_and_journal(connected_app, monkeypatch):
    app, client, api, token = connected_app
    handle(app, update("/start " + issue(client, token)))
    assert confirm(client, token).status_code == 303
    now = datetime(2026, 1, 1, 6, tzinfo=UTC)

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr("app.bot.handler.datetime", FixedTime)
    sender = Sender()
    engine = NotificationEngine(
        app.state.db_engine,
        base_url="https://example.test",
        sender=sender,
        clock=lambda: now,
    )
    with engine.session() as db, db.begin():
        create_birthday(db, name="Алёна Ёжик", day=1, month=1)
    assert engine.tick() == 1
    handle(app, update("🎂 Добавить"))
    with engine.session() as db:
        snapshot = db.get(BotDraft, 1).payload
    runtime = BotRuntime(api, app.state.db_engine, "https://example.test")
    api.updates = [callback_update("evening:1", id_=100)]
    runtime.poll_once()
    runtime.poll_once()
    with engine.session() as db:
        assert len(list(db.scalars(select(EveningReminder)))) == 1
        assert db.get(BotDraft, 1).payload == snapshot
    assert "недействительна" in handle(app, callback_update("evening:wrong"))[-1].text
    assert "недоступно" in handle(app, callback_update("evening:999"))[-1].text
    page = client.get("/admin/deliveries?kind=evening&status=pending")
    assert page.status_code == 200
    assert "Алёна Ёжик" in page.text and "01.01.2026 19:00" in page.text
    assert (
        "<h2>Алёна Ёжик</h2>" not in client.get("/admin/deliveries?kind=evening&status=failed").text
    )
    assert client.get("/admin/deliveries?kind=bad").status_code == 400
    # The callback during a draft did not replace its keyboard.
    assert api.sent[-1][2] is None


def test_api_evening_button(evening):
    _, engine, clock, sender = evening
    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI(SecretStr(TOKEN), transport=httpx.MockTransport(transport))
    try:
        api.send(sender.messages[0])
        assert captured[-1]["reply_markup"]["inline_keyboard"][1][0]["callback_data"] == "evening:1"
        request(engine, clock)
        clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
        engine.tick()
        api.send(sender.messages[-1])
        assert len(captured[-1]["reply_markup"]["inline_keyboard"]) == 1
    finally:
        api.close()


def test_migration_preserves_original_history(evening):
    _, engine, clock, _ = evening
    path = Path(engine.db_engine.url.database)
    config = migration_config()
    with engine.db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005_bot_drafts")
    backup = upgrade_database(path)
    assert backup
    with sqlite3.connect(backup) as db:
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005_bot_drafts"
        )
        assert db.execute("SELECT status FROM deliveries").fetchone()[0] == "sent"
    request(engine, clock)
    with engine.db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.check(config)
        command.downgrade(config, "0005_bot_drafts")
        assert connection.exec_driver_sql("SELECT status FROM deliveries").scalar() == "sent"
        assert (
            connection.exec_driver_sql("SELECT outcome FROM delivery_attempts").scalar() == "sent"
        )


def test_evening_retry_limit(evening):
    _, engine, clock, sender = evening
    request(engine, clock)
    clock.value = datetime(2026, 1, 1, 16, tzinfo=UTC)
    sender.error = RetryableError()
    for attempt in range(1, 6):
        assert engine.tick() == 1
        row = get_evening(engine)
        assert row.attempts == attempt
        clock.value = row.next_attempt_at.replace(tzinfo=UTC)
    assert get_evening(engine).status == "failed"
    assert engine.tick() == 0
