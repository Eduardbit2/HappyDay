import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_telegram import confirm, handle, issue, update
from test_telegram import connected_app as connected_app

from app.auth.security import utc_now
from app.birthdays.service import create_birthday
from app.bot.polling import BotRuntime
from app.db.models import Birthday, BotDraft, Group, TelegramLink, User


@pytest.fixture
def bot(connected_app):
    app, client, api, csrf_value = connected_app
    code = issue(client, csrf_value)
    handle(app, update("/start " + code))
    assert confirm(client, csrf_value).status_code == 303
    return app, api


def message(app, text):
    return handle(app, update(text))[-1]


def button(reply, text=None):
    buttons = [b for row in reply.markup["inline_keyboard"] for b in row]
    return next(b["callback_data"] for b in buttons if text is None or b["text"] == text)


def callback_update(data, chat_id=54321, id_=10):
    return {
        "update_id": id_,
        "callback_query": {
            "id": str(id_),
            "data": data,
            "from": {"id": chat_id, "is_bot": False},
            "message": {"chat": {"id": chat_id, "type": "private"}, "message_id": 1},
        },
    }


def click(app, data):
    return handle(app, callback_update(data))[-1]


def birthdays(app):
    with Session(app.state.db_engine) as db:
        return list(db.scalars(select(Birthday).order_by(Birthday.id)))


def test_full_add_validation_and_utf8(bot):
    app, _api = bot
    assert "Как зовут" in message(app, "🎂 Добавить").text
    message(app, "  Алёна   Ёжик 🎂 ")
    assert "Введите дату" in message(app, "не дата").text
    assert "Такой даты нет" in message(app, "31.04").text
    message(app, "29.02")
    assert "Проверьте год" in message(app, "1900").text
    message(app, "Пропустить")
    assert "Группа не найдена" in message(app, "999").text
    message(app, "Без группы")
    preview = message(app, "Любит чай; торт\nИ свечи 🎂")
    assert "год неизвестен" in preview.text
    assert birthdays(app) == []
    assert "сохранен" in click(app, button(preview)).text
    row = birthdays(app)[0]
    assert row.name == "Алёна Ёжик 🎂" and row.year is None
    assert row.note == "Любит чай; торт\nИ свечи 🎂"
    with Session(app.state.db_engine) as db:
        assert db.get(BotDraft, 1) is None


def test_quick_input_back_invalidates_old_buttons_and_replay(bot):
    app, _api = bot
    preview = message(app, "Иван Петров 17.05.1985")
    stale = button(preview)
    assert len(stale.encode()) <= 64
    message(app, "↩️ Назад")
    assert "Кнопка устарела" in click(app, stale).text
    preview = message(app, "Новая заметка")
    saved = button(preview)
    click(app, saved)
    assert "Кнопка устарела" in click(app, saved).text
    assert len(birthdays(app)) == 1 and birthdays(app)[0].note == "Новая заметка"


def test_duplicate_requires_separate_confirmation_including_archive(bot):
    app, _api = bot
    with Session(app.state.db_engine) as db, db.begin():
        person = create_birthday(db, name="Иван Петров", day=17, month=5, year=1985)
        person.is_active = False
    preview = message(app, "Иван Петров 17.05.1985")
    forged = button(preview).replace(":save", ":duplicate")
    assert "кнопка" in click(app, forged).text
    assert len(birthdays(app)) == 1
    duplicate = click(app, button(preview))
    assert "Такое имя и дата" in duplicate.text
    assert len(birthdays(app)) == 1
    click(app, button(duplicate))
    assert len(birthdays(app)) == 2


def test_edit_keeps_fields_and_excludes_self_duplicate(bot):
    app, _api = bot
    with Session(app.state.db_engine) as db, db.begin():
        person = create_birthday(db, name="Иван", day=17, month=5, year=1985, note="Заметка")
        id_ = person.id
    result = message(app, "Иван")
    assert button(result, "✏️ Изменить") == f"edit:{id_}"
    click(app, f"edit:{id_}")
    message(app, "Пётр")
    for _ in range(4):
        preview = message(app, "Оставить как есть")
    click(app, button(preview))
    person = birthdays(app)[0]
    assert person.id == id_ and person.name == "Пётр" and person.note == "Заметка"
    assert (person.day, person.month, person.year) == (17, 5, 1985)
    click(app, f"edit:{id_}")
    for _ in range(5):
        preview = message(app, "Оставить как есть")
    assert "сохранен" in click(app, button(preview)).text
    assert len(birthdays(app)) == 1


@pytest.mark.parametrize("change", ["name", "archive", "delete"])
def test_concurrent_edit_does_not_overwrite(bot, change):
    app, _api = bot
    with Session(app.state.db_engine) as db, db.begin():
        person = create_birthday(db, name="Иван", day=1, month=1)
        id_ = person.id
    click(app, f"edit:{id_}")
    for _ in range(5):
        preview = message(app, "Оставить как есть")
    with Session(app.state.db_engine) as db, db.begin():
        person = db.get(Birthday, id_)
        if change == "name":
            person.name = "Уже изменено"
        elif change == "archive":
            person.is_active = False
        else:
            db.delete(person)
    assert "уже изменена или удалена" in click(app, button(preview)).text
    with Session(app.state.db_engine) as db:
        assert db.get(BotDraft, 1) is None


def test_archive_confirm_and_cancel(bot):
    app, _api = bot
    with Session(app.state.db_engine) as db, db.begin():
        person = create_birthday(db, name="Иван", day=1, month=1)
        id_ = person.id
    preview = click(app, f"archive:{id_}")
    message(app, "✖️ Отмена")
    assert "Кнопка устарела" in click(app, button(preview)).text
    assert birthdays(app)[0].is_active
    preview = click(app, f"archive:{id_}")
    click(app, button(preview))
    assert not birthdays(app)[0].is_active


def test_expiry_and_unlink_discard_draft(bot):
    app, _api = bot
    message(app, "🎂 Добавить")
    with Session(app.state.db_engine) as db, db.begin():
        db.get(BotDraft, 1).expires_at = utc_now() - timedelta(seconds=1)
    assert "Черновик истек" in message(app, "Имя").text
    message(app, "🎂 Добавить")
    with Session(app.state.db_engine) as db, db.begin():
        db.delete(db.get(TelegramLink, 1))
    with Session(app.state.db_engine) as db:
        assert db.get(BotDraft, 1) is None
    assert birthdays(app) == []


def test_forged_cross_user_and_disabled_cannot_save(bot):
    app, _api = bot
    preview = message(app, "Иван 01.01")
    replies = handle(app, callback_update(button(preview), chat_id=77777))
    assert "создайте ссылку" in replies[0].text
    with Session(app.state.db_engine) as db, db.begin():
        db.get(User, 1).is_active = False
    assert "создайте ссылку" in click(app, button(preview)).text
    assert birthdays(app) == []


def test_poll_restart_preserves_draft_and_confirm_is_processed_once(bot):
    app, api = bot
    runtime = BotRuntime(api, app.state.db_engine, "https://example.test")
    api.updates = [update("🎂 Добавить", update_id=20), update("Алёна", update_id=21)]
    runtime.poll_once()
    with Session(app.state.db_engine) as db:
        draft = db.get(BotDraft, 1)
        assert draft.step == "date"
        assert json.loads(draft.payload)["values"]["name"] == "Алёна"
    runtime = BotRuntime(api, app.state.db_engine, "https://example.test")
    api.updates = [
        update(text, update_id=id_)
        for id_, text in enumerate(("29.02", "Пропустить", "Без группы", "Пропустить"), 22)
    ]
    runtime.poll_once()
    callback_data = api.sent[-1][2]["inline_keyboard"][0][0]["callback_data"]
    original_call = api.call
    answers = []

    def call(method, payload=None):
        if method == "answerCallbackQuery":
            answers.append(payload)
            return True
        return original_call(method, payload)

    api.call = call
    api.updates = [callback_update(callback_data, id_=26)]
    runtime.poll_once()
    runtime.poll_once()
    assert len(birthdays(app)) == 1
    assert len(answers) == 1


def test_group_removed_after_preview_is_not_silently_replaced(bot):
    app, _api = bot
    with Session(app.state.db_engine) as db, db.begin():
        group = Group(name="Друзья")
        db.add(group)
        db.flush()
        id_ = group.id
    message(app, "/add")
    for text in ("Иван", "01.01", "Пропустить", str(id_)):
        message(app, text)
    preview = message(app, "Пропустить")
    with Session(app.state.db_engine) as db, db.begin():
        db.delete(db.get(Group, id_))
    assert "Проверьте дату и группу" in click(app, button(preview)).text
    assert birthdays(app) == []


def test_lost_reply_after_save_does_not_repeat_mutation(bot):
    app, api = bot
    preview = message(app, "Алёна 01.01")
    api.updates = [callback_update(button(preview), id_=100)]
    original_call = api.call

    def call(method, payload=None):
        return True if method == "answerCallbackQuery" else original_call(method, payload)

    def disconnected(*args):
        raise TimeoutError("test only")

    api.call, api.send_text = call, disconnected
    runtime = BotRuntime(api, app.state.db_engine, "https://example.test")
    runtime.poll_once()
    runtime.poll_once()
    assert len(birthdays(app)) == 1
    with Session(app.state.db_engine) as db:
        assert db.get(BotDraft, 1) is None
