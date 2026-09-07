from sqlalchemy import select
from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_auth import csrf, login, post

from app.db.models import Birthday


def test_add_unknown_year_and_search(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    result = post(
        client,
        "/birthdays/new",
        token,
        name="Алёна Ёжик 🎂",
        day="29",
        month="2",
        year="",
        group_id="1",
        note="Любит чай",
    )
    assert result.status_code == 303
    detail = client.get(result.headers["location"])
    assert "День рождения сохранен" in detail.text
    assert "Алёна Ёжик 🎂" in detail.text
    assert "Исполнится" not in detail.text
    assert client.get("/birthdays?q=ёжик&group=&month=").status_code == 200
    assert "Алёна Ёжик 🎂" in client.get("/birthdays?q=ЁЖИК").text
    assert "Ничего не найдено" in client.get("/birthdays?q=неизвестный").text
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Birthday)).year is None


def test_invalid_date_preserves_form(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    response = post(client, "/birthdays/new", token, name="Иван", day="31", month="4")
    assert response.status_code == 400
    assert 'value="Иван"' in response.text
    assert 'aria-invalid="true"' in response.text
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Birthday)) is None


def test_duplicate_needs_confirmation(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    fields = dict(name="Иван", day="1", month="1")
    assert post(client, "/birthdays/new", token, **fields).status_code == 303
    response = post(client, "/birthdays/new", token, **fields)
    assert response.status_code == 400
    assert "confirm_duplicate" in response.text
    assert post(client, "/birthdays/new", token, confirm_duplicate="1", **fields).status_code == 303
    with Session(app.state.db_engine) as db:
        assert len(list(db.scalars(select(Birthday)))) == 2


def test_calendar_pages_require_auth_and_csrf(auth_app):
    _app, client = auth_app
    for path in ("/birthdays", "/birthdays/new", "/settings", "/groups"):
        assert client.get(path, follow_redirects=False).status_code == 303
    login(client)
    assert (
        post(client, "/birthdays/new", "invalid", name="Иван", day="1", month="1").status_code
        == 403
    )
    assert client.get("/birthdays/9999").status_code == 404


def test_groups_and_empty_filters(auth_app):
    _app, client = auth_app
    login(client)
    token = csrf(client.get("/groups"))
    assert post(client, "/groups", token, name="Друзья").status_code == 303
    assert "Друзья" in client.get("/groups").text
    assert client.get("/birthdays?q=&group=&month=").status_code == 200
    assert "Пока никого нет" in client.get("/").text
