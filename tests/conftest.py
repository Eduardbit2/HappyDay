import pytest


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Рабочее окружение пользователя не влияет на тесты."""
    import os

    for name in os.environ:
        if name.startswith("APP_"):
            monkeypatch.delenv(name)
