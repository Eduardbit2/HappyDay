import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_auth import csrf, login, post

from app.birthdays.csv_io import escape_cell, parse_csv, unescape_cell
from app.birthdays.service import create_birthday
from app.db.models import Birthday, CsvDraft, Delivery, DeliveryAttempt, Group

HEADER = "name;day;month;year;group;note;active\n"
CSV = HEADER + 'Алёна Ёжик 🎂;29;2;;Друзья;"Чай; торт\nИ свечи";1\n'


def add(client, token, name="Иван"):
    response = post(client, "/birthdays/new", token, name=name, day="1", month="1")
    assert response.status_code == 303
    return response.headers["location"].split("?")[0]


def draft_token(response):
    assert response.status_code == 200, response.text
    return re.search(r'name="draft_token" value="([^"]+)"', response.text).group(1)


def test_edit_archive_restore_and_confirmed_delete(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    path = add(client, token)
    assert "Изменить день рождения" in client.get(path + "/edit").text
    assert post(client, path + "/edit", token, name="Иван", day="1", month="1").status_code == 303
    assert post(client, path + "/edit", token, name="Алёна", day="31", month="4").status_code == 400
    assert "Иван" in client.get(path).text
    assert post(client, path + "/delete", token, confirm="1").status_code == 400
    assert post(client, path + "/archive", "invalid").status_code == 403
    assert post(client, path + "/archive", token).status_code == 303
    assert "Иван" not in client.get("/birthdays").text
    assert "Иван" in client.get("/birthdays/archive").text
    assert post(client, path + "/restore", token).status_code == 303
    assert "Иван" in client.get("/birthdays").text
    post(client, path + "/archive", token)
    assert "история напоминаний" in client.get(path + "/delete").text
    assert post(client, path + "/delete", token).status_code == 400
    assert post(client, path + "/delete", token, confirm="1").status_code == 303
    assert client.get(path).status_code == 404
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Birthday)) is None


def test_edit_duplicate_including_archive(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    first, second = add(client, token, "Алёна"), add(client, token, "Иван")
    post(client, first + "/archive", token)
    fields = dict(name="АЛЁНА", day="1", month="1")
    assert post(client, second + "/edit", token, **fields).status_code == 400
    with Session(app.state.db_engine) as db:
        assert db.get(Birthday, int(second.rsplit("/", 1)[1])).name == "Иван"
    assert post(client, second + "/edit", token, **fields, confirm_duplicate="1").status_code == 303


def test_group_transfer_includes_archived_and_system_protected(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/groups"))
    post(client, "/groups", token, name="Друзья")
    with Session(app.state.db_engine) as db, db.begin():
        group_id = db.scalar(select(Group.id).where(Group.name == "Друзья"))
        active = create_birthday(db, name="Лёля", day=1, month=1, group_id=group_id)
        archived = create_birthday(db, name="Пётр", day=2, month=1, group_id=group_id)
        archived.is_active = False
        ids = (active.id, archived.id)
    fields = dict(name="Близкие", icon="🎂", color="#728C72", sort_order="5")
    assert post(client, f"/groups/{group_id}/edit", token, **fields).status_code == 303
    assert "Близкие" in client.get("/groups").text
    assert post(client, "/groups/1/edit", token, **fields).status_code == 400
    assert (
        post(client, "/groups/1/delete", token, confirm="1", transfer_to=str(group_id)).status_code
        == 400
    )
    assert (
        post(
            client, f"/groups/{group_id}/delete", token, confirm="1", transfer_to="999"
        ).status_code
        == 400
    )
    assert (
        post(client, f"/groups/{group_id}/delete", token, confirm="1", transfer_to="1").status_code
        == 303
    )
    with Session(app.state.db_engine) as db:
        assert db.get(Group, group_id) is None
        assert all(db.get(Birthday, id_).group_id == 1 for id_ in ids)
        assert not db.get(Birthday, ids[1]).is_active


def test_archive_cancels_pending_and_delete_cascades_history(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/birthdays/new"))
    path = add(client, token)
    id_ = int(path.rsplit("/", 1)[1])
    with Session(app.state.db_engine) as db, db.begin():
        for offset, status in ((1, "pending"), (0, "sent")):
            delivery = Delivery(
                birthday_id=id_,
                occurrence_date=datetime.now().date(),
                days_before=offset,
                user_id=1,
                status=status,
                attempts=1,
                next_attempt_at=datetime.now(),
                sent_at=datetime.now() if status == "sent" else None,
            )
            db.add(delivery)
            db.flush()
            db.add(DeliveryAttempt(delivery_id=delivery.id, attempt_number=1, outcome="sent"))
    post(client, path + "/archive", token)
    with Session(app.state.db_engine) as db:
        assert set(db.scalars(select(Delivery.status))) == {"cancelled", "sent"}
    post(client, path + "/delete", token, confirm="1")
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Delivery)) is None
        assert db.scalar(select(DeliveryAttempt)) is None


@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_csv_preview_import_once_and_export_utf8(auth_app, bom):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/data"))
    preview = post(client, "/data/preview", token, csv=bom + CSV)
    draft = draft_token(preview)
    assert "Алёна Ёжик 🎂" in preview.text
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Birthday)) is None
        assert len(list(db.scalars(select(Group)))) == 1
    assert post(client, "/data/import", token, draft_token=draft).status_code == 400
    assert post(client, "/data/import", token, draft_token=draft, confirm="1").status_code == 303
    assert post(client, "/data/import", token, draft_token=draft, confirm="1").status_code == 400
    export = client.get("/data/export")
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = parse_csv(export.content.decode("utf-8"))
    assert rows[0]["name"] == "Алёна Ёжик 🎂"
    assert rows[0]["note"] == "Чай; торт\nИ свечи"
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(CsvDraft)) is None
        assert len(list(db.scalars(select(Birthday)))) == 1


def test_csv_errors_no_partial_data_and_duplicate_recheck(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/data"))
    invalid = CSV + "Ошибка;31;4;;Новая;;1\n"
    assert post(client, "/data/preview", token, csv=invalid).status_code == 400
    with Session(app.state.db_engine) as db:
        assert db.scalar(select(Birthday)) is None
        assert db.scalar(select(CsvDraft)) is None
    draft = draft_token(post(client, "/data/preview", token, csv=CSV))
    with Session(app.state.db_engine) as db, db.begin():
        create_birthday(db, name="Алёна Ёжик 🎂", day=29, month=2)
    response = post(client, "/data/import", token, draft_token=draft, confirm="1")
    assert response.status_code == 400
    assert "Возможный дубль" in response.text
    assert (
        post(
            client, "/data/import", token, draft_token=draft, confirm="1", confirm_duplicates="1"
        ).status_code
        == 303
    )


def test_csv_draft_bound_to_session_and_expires(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/data"))
    draft = draft_token(post(client, "/data/preview", token, csv=CSV))
    old_cookies = dict(client.cookies)
    client.cookies.clear()
    login(client)
    new_token = csrf(client.get("/data"))
    assert (
        post(client, "/data/import", new_token, draft_token=draft, confirm="1").status_code == 400
    )
    client.cookies.clear()
    client.cookies.update(old_cookies)
    with Session(app.state.db_engine) as db, db.begin():
        db.scalar(select(CsvDraft)).expires_at = datetime.now() - timedelta(days=1)
    assert post(client, "/data/import", token, draft_token=draft, confirm="1").status_code == 400


@pytest.mark.parametrize(
    "value", ["=1+1", "+SUM(A1)", "-1", "@A1", "'Иван", "  =1", "\t=1", "Обычный", "", "  "]
)
def test_csv_formula_escape_round_trip(value):
    escaped = escape_cell(value)
    assert unescape_cell(escaped) == value
    if value.lstrip() and value.lstrip()[0] in "=+-@":
        assert escaped.startswith("'")


def test_csv_limits_and_protection(auth_app):
    _app, client = auth_app
    assert client.get("/data/export", follow_redirects=False).status_code == 303
    login(client)
    token = csrf(client.get("/data"))
    assert post(client, "/data/preview", "invalid", csv=CSV).status_code == 403
    assert post(client, "/data/preview", token, csv="x" * 262145).status_code == 400
    assert (
        post(client, "/data/preview", token, csv=HEADER + "Иван;1;1;;;;1\n" * 501).status_code
        == 400
    )
