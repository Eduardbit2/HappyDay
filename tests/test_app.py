import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_lifecycle_and_utf8():
    app = create_app(Settings(_env_file=None))
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
        response = client.get("/")
        assert response.status_code == 200
        assert "Семейные дни рождения" in response.content.decode("utf-8")
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
