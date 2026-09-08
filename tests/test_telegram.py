import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_auth import csrf, login, post

from app.auth.security import utc_now
from app.birthdays.service import create_birthday
from app.bot.api import TelegramAPI
from app.bot.handler import handle_update
from app.bot.linking import claim_pairing
from app.bot.polling import BotRuntime
from app.config import Settings
from app.db.models import BotCursor, TelegramLink, TelegramPairRequest, User
from app.notifications.engine import Message, PermanentError, RetryableError, UnknownResultError

TOKEN = "12345:" + "A" * 35


class FakeAPI:
    bot_id = "12345"

    def __init__(self):
        self.sent = []
        self.updates = []
        self.offsets = []

    def call(self, method, payload=None):
        if method == "getMe":
            return {"id": 12345, "username": "happyday_test_bot", "is_bot": True}
        if method == "getUpdates":
            self.offsets.append(payload["offset"])
            return self.updates
        raise AssertionError(method)

    def send_text(self, chat_id, text, markup=None):
        self.sent.append((chat_id, text, markup))


@pytest.fixture
def connected_app(auth_app):
    app, client = auth_app
    api = FakeAPI()
    app.state.telegram_runtime = SimpleNamespace(
        api=api, username="happyday_test_bot", status="ready"
    )
    login(client)
    token = csrf(client.get("/profile"))
    return app, client, api, token


def issue(client, csrf_value):
    response = post(client, "/profile/telegram/link", csrf_value)
    assert response.status_code == 200
    return re.search(r"\?start=([A-Za-z0-9_-]{43})", response.text).group(1)


def update(text, *, chat_id=54321, private=True, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": "private" if private else "group"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Алёна Ёжик 🎂"},
            "text": text,
        },
    }


def handle(app, incoming):
    with Session(app.state.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
        return handle_update(db, incoming, "https://example.test")


def confirm(client, token):
    page = client.get("/profile/telegram")
    request_hash = re.search(r'name="request_hash" value="([^"]+)"', page.text).group(1)
    return post(
        client, "/profile/telegram/confirm", token, request_hash=request_hash, telegram_id="54321"
    )


def test_link_requires_web_confirmation_and_unlink(connected_app):
    app, client, api, csrf_value = connected_app
    code = issue(client, csrf_value)
    with Session(app.state.db_engine) as db:
        request = db.scalar(select(TelegramPairRequest))
        assert code not in request.token_hash
    assert handle(app, update("/start " + code, private=False)) == []
    reply = handle(app, update("/start " + code))
    assert "подтвердите" in reply[0].text
    with Session(app.state.db_engine) as db:
        assert db.get(TelegramLink, 1) is None
    assert "создайте ссылку" in handle(app, update("👥 Все"))[0].text
    assert "Это мой Telegram" in client.get("/profile/telegram").text
    assert confirm(client, csrf_value).status_code == 303
    assert "Выберите действие" in handle(app, update("/start"))[0].text
    assert post(client, "/profile/telegram/test", csrf_value).status_code == 303
    assert len(api.sent) == 1 and api.sent[0][0] == 54321
    assert post(client, "/profile/telegram/unlink", csrf_value).status_code == 400
    assert post(client, "/profile/telegram/unlink", csrf_value, confirm="1").status_code == 303
    assert "создайте ссылку" in handle(app, update("👥 Все"))[0].text


def test_expired_replaced_and_replayed_codes(connected_app):
    app, client, _api, csrf_value = connected_app
    old = issue(client, csrf_value)
    new = issue(client, csrf_value)
    assert "устарела" in handle(app, update("/start " + old))[0].text
    assert "подтвердите" in handle(app, update("/start " + new))[0].text
    assert "устарела" in handle(app, update("/start " + new))[0].text
    with Session(app.state.db_engine) as db, db.begin():
        db.get(TelegramPairRequest, 1).expires_at = utc_now() - timedelta(seconds=1)
    assert post(client, "/profile/telegram/confirm", csrf_value).status_code == 400


def test_stale_confirmation_and_csrf(connected_app):
    app, client, _api, csrf_value = connected_app
    assert post(client, "/profile/telegram/link", "invalid").status_code == 403
    code = issue(client, csrf_value)
    handle(app, update("/start " + code))
    assert (
        post(
            client,
            "/profile/telegram/confirm",
            csrf_value,
            request_hash="wrong",
            telegram_id="54321",
        ).status_code
        == 400
    )
    with Session(app.state.db_engine) as db:
        assert db.get(TelegramLink, 1) is None


def test_concurrent_claim_only_one_account(connected_app):
    app, client, _api, csrf_value = connected_app
    code = issue(client, csrf_value)

    def claim(id_):
        with Session(app.state.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
            return claim_pairing(db, code, id_, "Тест")

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(claim, (54321, 54322))) == 1


def test_revoked_origin_session_removes_pairing(connected_app):
    app, client, _api, csrf_value = connected_app
    code = issue(client, csrf_value)
    post(client, "/logout-all", csrf_value)
    assert "устарела" in handle(app, update("/start " + code))[0].text
    with Session(app.state.db_engine) as db:
        assert db.get(TelegramPairRequest, 1) is None


def test_private_calendar_requires_active_link(connected_app):
    app, client, _api, csrf_value = connected_app
    code = issue(client, csrf_value)
    handle(app, update("/start " + code))
    confirm(client, csrf_value)
    with Session(app.state.db_engine) as db, db.begin():
        create_birthday(db, name="Семейный Ёжик 🎂", day=29, month=2)
        archived = create_birthday(db, name="Архивная запись", day=1, month=1)
        archived.is_active = False
    text = "\n".join(reply.text for reply in handle(app, update("Ёжик")))
    assert "Семейный Ёжик 🎂" in text and "Возраст" not in text
    assert "Архивная запись" not in text
    with Session(app.state.db_engine) as db, db.begin():
        db.get(User, 1).is_active = False
    assert "Семейный Ёжик" not in handle(app, update("👥 Все"))[0].text


def test_polling_persists_offset_and_does_not_replay(connected_app):
    app, client, api, csrf_value = connected_app
    code = issue(client, csrf_value)
    api.updates = [update("/start " + code, update_id=10)]
    runtime = BotRuntime(api, app.state.db_engine, "https://example.test")
    runtime.identify()
    runtime.poll_once()
    assert len(api.sent) == 1
    restarted = BotRuntime(api, app.state.db_engine, "https://example.test")
    restarted.poll_once()
    assert api.offsets == [0, 11]
    assert len(api.sent) == 1
    with Session(app.state.db_engine) as db:
        assert db.get(BotCursor, "12345").next_update_id == 11


@pytest.mark.parametrize(
    "incoming",
    [
        {"message": None},
        {"message": {"chat": None}},
        {"message": {"chat": {}, "from": None}},
        {"message": {"chat": {"id": -1, "type": "group"}, "text": "/start"}},
        {"callback_query": {"data": "delete:1"}},
    ],
)
def test_unsupported_or_forged_updates_are_ignored(connected_app, incoming):
    app, _client, _api, _csrf = connected_app
    assert handle(app, incoming) == []


@pytest.mark.parametrize(
    "code,error",
    [
        (400, PermanentError),
        (401, PermanentError),
        (403, PermanentError),
        (429, RetryableError),
        (500, UnknownResultError),
    ],
)
def test_api_classifies_failures_without_exposing_token(code, error):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            code,
            json={
                "ok": False,
                "error_code": code,
                "description": TOKEN,
                "parameters": {"retry_after": 90},
            },
        )
    )
    api = TelegramAPI(SecretStr(TOKEN), transport=transport)
    try:
        with pytest.raises(error) as failure:
            api.send_text(54321, "Привет 🎂")
        assert TOKEN not in str(failure.value)
        if code == 429:
            assert failure.value.retry_after == 90
    finally:
        api.close()


def test_api_request_utf8_and_notification_button():
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI(SecretStr(TOKEN), transport=httpx.MockTransport(respond))
    try:
        api.send(Message(1, 1, 54321, "Алёна Ёжик 🎂", "https://example.test/birthdays/1"))
        assert captured[0]["text"] == "Алёна Ёжик 🎂"
        assert "parse_mode" not in captured[0]
        assert captured[0]["reply_markup"]["inline_keyboard"][0][0]["text"] == "🌐 Открыть"
    finally:
        api.close()


def test_timeout_is_uncertain_not_retryable():
    def timeout(request):
        raise httpx.ReadTimeout(TOKEN)

    api = TelegramAPI(SecretStr(TOKEN), transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(UnknownResultError) as failure:
            api.send_text(54321, "Тест")
        assert TOKEN not in str(failure.value)
    finally:
        api.close()


def test_token_is_hidden_in_settings_and_validation_errors():
    settings = Settings(_env_file=None, telegram_token=TOKEN)
    assert TOKEN not in repr(settings)
    assert Settings(_env_file=None, telegram_token="").telegram_token is None
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, telegram_token="invalid-secret-value")
    assert "invalid-secret-value" not in str(failure.value)
