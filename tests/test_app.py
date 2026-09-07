import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_lifecycle_and_utf8(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
        response = client.get("/login")
        assert response.status_code == 200
        assert "Войти в HappyDay" in response.content.decode("utf-8")
        assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400
        assert client.get("/docs").status_code == 404
    assert app.state.ready is False
    with TestClient(app, base_url="http://localhost") as client:
        app.state.ready = False
        assert client.get("/health/ready").status_code == 503


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_hosts": ["*"]},
        {"allowed_hosts": []},
        {"env": "production"},
        {"env": "production", "base_url": "https://happyday.example"},
    ],
)
def test_invalid_configuration(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_production_configuration():
    settings = Settings(
        _env_file=None,
        env="production",
        base_url="https://happyday.example",
        allowed_hosts=["happyday.example", "localhost"],
    )
    assert settings.base_url.scheme == "https"


def test_env_file_utf8(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_DATA_DIR=./данные/Семья-Ёжики-🎂\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.data_dir.name == "Семья-Ёжики-🎂"


def test_readiness_database_failure(tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    app = create_app(Settings(_env_file=None, data_dir=tmp_path))

    def database_failure(_engine):
        raise SQLAlchemyError("internal database details")

    with TestClient(app, base_url="http://localhost") as client:
        monkeypatch.setattr("app.web.routes.schema_is_current", database_failure)
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert client.get("/health/live").status_code == 200


def test_failed_migration_prevents_start(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))

    def fail_upgrade(_path):
        raise RuntimeError("Migration failed")

    monkeypatch.setattr("app.main.upgrade_database", fail_upgrade)
    with pytest.raises(RuntimeError, match="Migration failed"), TestClient(app):
        pass
    assert app.state.ready is False
