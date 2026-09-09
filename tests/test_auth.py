import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.security import token_hash, utc_now, verify_password
from app.auth.web import COOKIE_NAME
from app.config import Settings
from app.db.migrations import migration_config, upgrade_database
from app.db.models import AuthSession, Invitation, User
from app.main import create_app

PASSWORD = "Тестовый-пароль-123"
NEW_PASSWORD = "Совсем-новый-пароль-456"


def csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def post(client, path, csrf_value, **fields):
    return client.post(
        path,
        data={"csrf_token": csrf_value, **fields},
        headers={"Origin": "http://localhost"},
        follow_redirects=False,
    )


def login(client, login="admin", password=PASSWORD):
    token = csrf(client.get("/login"))
    return post(client, "/login", token, login=login, password=password)


@pytest.fixture
def auth_app(tmp_path):
    # Web/bot tests drive notification engines with their own clocks.
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, scheduler_enabled=False))
    with TestClient(app, base_url="http://localhost") as client:
        with Session(app.state.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
            service.bootstrap_admin(db, name="Алёна Ёжик", login="Admin", password=PASSWORD)
            service.create_user(db, name="Пётр", login="пётр", password=PASSWORD)
        yield app, client


def test_login_rotation_and_password_storage(auth_app):
    app, client = auth_app
    response = client.get("/login")
    old_cookie = client.cookies.get(COOKIE_NAME)
    result = post(client, "/login", csrf(response), login="ADMIN", password=PASSWORD)
    assert result.status_code == 303
    assert result.headers["location"] == "/"
    assert old_cookie != client.cookies.get(COOKIE_NAME)
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    assert "Алёна Ёжик" in client.get("/").text
    with Session(app.state.db_engine) as db:
        user = db.scalar(select(User).where(User.login == "admin"))
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
        assert verify_password(user.password_hash, PASSWORD)
        assert db.get(AuthSession, token_hash(old_cookie)) is None
        assert db.get(AuthSession, token_hash(client.cookies.get(COOKIE_NAME))) is not None


@pytest.mark.parametrize("path", ["/", "/profile", "/admin"])
def test_anonymous_pages_require_login(auth_app, path):
    _app, client = auth_app
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_member_cannot_manage_users_or_invites(auth_app):
    _app, client = auth_app
    assert login(client, "ПЁТР").status_code == 303
    assert client.get("/admin").status_code == 403
    token = csrf(client.get("/profile"))
    assert post(client, "/admin/invitations", token).status_code == 403
    assert post(client, "/admin/users/1", token, role="member").status_code == 403


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://localhost.evil.example", "null", ""]
)
def test_origin_rejected(auth_app, origin):
    _app, client = auth_app
    token = csrf(client.get("/login"))
    response = client.post(
        "/login",
        data={"csrf_token": token, "login": "admin", "password": PASSWORD},
        headers={"Origin": origin},
    )
    assert response.status_code == 403


def test_csrf_invalid_missing_and_wrong_session(auth_app):
    app, client = auth_app
    csrf(client.get("/login"))
    for token in ("", "forged"):
        assert post(client, "/login", token, login="admin", password=PASSWORD).status_code == 403
    with TestClient(app, base_url="http://localhost") as other:
        foreign = csrf(other.get("/login"))
        assert post(client, "/login", foreign, login="admin", password=PASSWORD).status_code == 403


def test_rate_limit_persists_across_sessions(auth_app):
    app, client = auth_app
    token = csrf(client.get("/login"))
    for _ in range(5):
        assert post(client, "/login", token, login="admin", password="wrong").status_code == 400
    response = post(client, "/login", token, login="admin", password=PASSWORD)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "900"
    with TestClient(app, base_url="http://localhost") as other:
        assert login(other).status_code == 429


def test_logout_all_revokes_other_device(auth_app):
    app, client = auth_app
    login(client)
    with TestClient(app, base_url="http://localhost") as other:
        login(other)
        assert post(client, "/logout-all", csrf(client.get("/profile"))).status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 303
        assert other.get("/", follow_redirects=False).status_code == 303


def test_logout_only_current_device(auth_app):
    app, client = auth_app
    login(client)
    with TestClient(app, base_url="http://localhost") as other:
        login(other)
        assert post(client, "/logout", csrf(client.get("/profile"))).status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 303
        assert other.get("/").status_code == 200


def test_expired_session(auth_app):
    app, client = auth_app
    login(client)
    key = token_hash(client.cookies.get(COOKIE_NAME))
    with Session(app.state.db_engine) as db, db.begin():
        db.get(AuthSession, key).expires_at = utc_now() - timedelta(seconds=1)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_invite_once_and_no_token_storage(auth_app):
    app, client = auth_app
    login(client)
    response = post(client, "/admin/invitations", csrf(client.get("/admin")), role="member")
    assert response.status_code == 200
    code = re.search(r'id="invitation-code"[^>]*>([^<]+)</textarea>', response.text).group(1)
    assert response.headers["cache-control"] == "no-store"
    with Session(app.state.db_engine) as db:
        invite = db.scalar(select(Invitation))
        assert invite.token_hash == token_hash(code)
        assert invite.token_hash != code
    with TestClient(app, base_url="http://localhost") as member:
        result = post(
            member,
            "/join",
            csrf(member.get("/join")),
            token=code,
            name="Лёля 🎂",
            login="лёля",
            password=PASSWORD,
            password_confirm=PASSWORD,
        )
        assert result.status_code == 303
        assert "Лёля 🎂" in member.get("/").text
    with TestClient(app, base_url="http://localhost") as other:
        result = post(
            other,
            "/join",
            csrf(other.get("/join")),
            token=code,
            name="Другой",
            login="other",
            password=PASSWORD,
            password_confirm=PASSWORD,
        )
        assert result.status_code == 400
    assert code not in client.get("/admin").text


@pytest.mark.parametrize("state", ["expired", "revoked", "issuer_disabled"])
def test_unusable_invitation(auth_app, state):
    app, _client = auth_app
    with Session(app.state.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
        admin = db.scalar(select(User).where(User.login == "admin"))
        invite, code = service.create_invitation(db, admin)
        if state == "expired":
            invite.expires_at = utc_now() - timedelta(seconds=1)
        elif state == "revoked":
            service.revoke_invitation(db, admin, invite.id)
        else:
            admin.is_active = False
    with (
        Session(app.state.db_engine.execution_options(sqlite_write=True)) as db,
        db.begin(),
        pytest.raises(ValueError),
    ):
        service.accept_invitation(db, token=code, name="Новый", login="new", password=PASSWORD)


def test_concurrent_invitation_acceptance(auth_app):
    app, _client = auth_app
    with Session(app.state.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
        _, code = service.create_invitation(
            db, db.scalar(select(User).where(User.login == "admin"))
        )

    def accept(index):
        try:
            with (
                Session(app.state.db_engine.execution_options(sqlite_write=True)) as db,
                db.begin(),
            ):
                service.accept_invitation(
                    db, token=code, name="Новый", login=f"new{index}", password=PASSWORD
                )
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, (1, 2))) == [False, True]


def test_last_admin_and_disable_member(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/admin"))
    assert post(client, "/admin/users/1", token, role="member", is_active="1").status_code == 400
    assert post(client, "/admin/users/1", token, role="admin").status_code == 400
    with TestClient(app, base_url="http://localhost") as member:
        login(member, "пётр")
        assert post(client, "/admin/users/2", token, role="member").status_code == 303
        assert member.get("/", follow_redirects=False).status_code == 303
        assert login(member, "пётр").status_code == 400


def test_change_password_revokes_all_sessions(auth_app):
    app, client = auth_app
    login(client)
    with TestClient(app, base_url="http://localhost") as other:
        login(other)
        result = post(
            client,
            "/profile/password",
            csrf(client.get("/profile")),
            current_password=PASSWORD,
            password=NEW_PASSWORD,
            password_confirm=NEW_PASSWORD,
        )
        assert result.status_code == 303
        assert other.get("/", follow_redirects=False).status_code == 303
        assert login(client, password=PASSWORD).status_code == 400
        assert login(client, password=NEW_PASSWORD).status_code == 303


def test_manual_account_and_duplicate_login(auth_app):
    app, client = auth_app
    login(client)
    token = csrf(client.get("/admin"))
    assert (
        post(
            client,
            "/admin/users",
            token,
            name="Ёжик",
            login="ЁЖИК",
            password=PASSWORD,
            role="member",
        ).status_code
        == 303
    )
    assert (
        post(
            client,
            "/admin/users",
            token,
            name="Еще",
            login="ёжик",
            password=PASSWORD,
            role="member",
        ).status_code
        == 400
    )
    with Session(app.state.db_engine) as db, db.begin(), pytest.raises(ValueError):
        service.bootstrap_admin(db, name="Другой", login="second", password=PASSWORD)


def test_secure_cookie_and_canonical_origin(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            env="production",
            base_url="https://happyday.example",
            allowed_hosts=["happyday.example"],
        )
    )
    with TestClient(app, base_url="https://happyday.example") as client:
        response = client.get("/login")
        assert "Secure" in response.headers["set-cookie"]
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "same-origin"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert (
            client.post(
                "/login",
                data={"csrf_token": csrf(response)},
                headers={"Origin": "http://happyday.example"},
            ).status_code
            == 403
        )


def test_auth_migration_preserves_existing_data(auth_app):
    app, _client = auth_app
    with app.state.db_engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0001_domain")
        connection.execute(text("INSERT INTO birthdays(name,day,month) VALUES ('Лёня',1,1)"))
    backup = upgrade_database(app.state.settings.data_dir / "happyday.db")
    assert backup is not None
    with app.state.db_engine.begin() as connection:
        assert connection.execute(text("SELECT name FROM birthdays")).scalar() == "Лёня"
        assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 2


def test_html_escaping_and_form_limits(auth_app):
    app, client = auth_app
    with Session(app.state.db_engine) as db, db.begin():
        db.get(User, 1).name = "<script>alert(1)</script>"
    login(client)
    assert "<script>alert(1)</script>" not in client.get("/").text
    assert "&lt;script&gt;" in client.get("/").text
    token = csrf(client.get("/profile"))
    assert post(client, "/logout", token, huge="x" * 17000).status_code == 413
    assert (
        client.post(
            "/logout",
            content=f"csrf_token={token}&csrf_token={token}",
            headers={
                "Origin": "http://localhost",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        ).status_code
        == 400
    )


def test_admin_password_reset(auth_app):
    app, client = auth_app
    login(client)
    with TestClient(app, base_url="http://localhost") as member:
        login(member, "пётр")
        result = post(
            client, "/admin/users/2/password", csrf(client.get("/admin")), password=NEW_PASSWORD
        )
        assert result.status_code == 303
        assert member.get("/", follow_redirects=False).status_code == 303
        assert login(member, "пётр", NEW_PASSWORD).status_code == 303
        result = post(
            member, "/admin/users/1/password", csrf(member.get("/profile")), password=NEW_PASSWORD
        )
        assert result.status_code == 403
