from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_telegram import confirm, handle, issue, update
from test_telegram import connected_app as connected_app

from app.birthdays.service import countdown, create_birthday, next_occurrence
from app.db.models import Birthday


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "Сегодня"),
        (1, "Завтра"),
        (2, "Через 2 дня"),
        (5, "Через 5 дней"),
        (11, "Через 11 дней"),
        (14, "Через 14 дней"),
        (21, "Через 21 день"),
        (22, "Через 22 дня"),
        (25, "Через 25 дней"),
        (111, "Через 111 дней"),
        (114, "Через 114 дней"),
        (121, "Через 121 день"),
    ],
)
def test_russian_countdown(days, expected):
    today = date(2026, 1, 1)
    assert countdown(today + timedelta(days=days), today) == expected


@pytest.mark.parametrize(
    ("today", "month", "day", "expected"),
    [
        (date(2026, 12, 31), 1, 1, date(2027, 1, 1)),
        (date(2026, 2, 28), 2, 29, date(2026, 2, 28)),
        (date(2026, 3, 1), 2, 29, date(2027, 2, 28)),
        (date(2028, 2, 28), 2, 29, date(2028, 2, 29)),
        (date(2028, 3, 1), 2, 29, date(2029, 2, 28)),
    ],
)
def test_next_birthday_calendar_edges(today, month, day, expected):
    assert next_occurrence(Birthday(day=day, month=month), today) == expected


def test_web_and_telegram_show_same_countdown(connected_app, monkeypatch):
    app, client, _api, token = connected_app
    handle(app, update("/start " + issue(client, token)))
    assert confirm(client, token).status_code == 303

    # UTC is still December 31, while Moscow has already entered January 1.
    class MoscowNewYear(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 31, 22, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr("app.bot.handler.datetime", MoscowNewYear)
    monkeypatch.setattr("app.web.birthdays.datetime", MoscowNewYear)
    with Session(app.state.db_engine) as db, db.begin():
        for day, name in [(1, "Алёна Ёжик"), (2, "Пётр"), (22, "Семён")]:
            create_birthday(db, name=name, day=day, month=1)

    for command in ("👥 Все", "📅 Ближайшие"):
        cards = handle(app, update(command))[1:]
        assert [card.text.splitlines()[-1] for card in cards] == [
            "Сегодня",
            "Завтра",
            "Через 21 день",
        ]
        assert "Алёна Ёжик" in cards[0].text
        assert "Возраст" not in cards[0].text
    for path in ("/", "/birthdays"):
        page = client.get(path)
        assert page.status_code == 200
        for label in ("Сегодня", "Завтра", "Через 21 день"):
            assert label in page.text
        assert 'class="row-countdown"' in page.text
        assert "Алёна Ёжик" in page.text
    assert "Через 21 день" in handle(app, update("Семён"))[1].text
    assert "Через 21 день" in client.get("/birthdays?q=Семён").text
