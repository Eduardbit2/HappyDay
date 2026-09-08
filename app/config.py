"""Проверяемая конфигурация; секреты не выводятся в журнал."""

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    base_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
    allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
    data_dir: Path = Path("data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    scheduler_enabled: bool = True

    session_hours: int = Field(default=168, ge=1, le=720)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_hosts(cls, hosts: list[str]) -> list[str]:
        if not hosts or any(not host.strip() or "*" in host for host in hosts):
            raise ValueError("Укажите явный список разрешенных хостов без wildcard")
        return hosts

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.env == "production":
            if self.base_url.scheme != "https":
                raise ValueError("В production APP_BASE_URL должен использовать HTTPS")
            if self.base_url.host not in self.allowed_hosts:
                raise ValueError("Хост APP_BASE_URL должен входить в APP_ALLOWED_HOSTS")
        return self
