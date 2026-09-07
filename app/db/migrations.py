"""Единая точка миграций: backup перед upgrade и проверка revision."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.db.engine import create_db_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR.resolve()))
    return config


def backup_database(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"happyday-{stamp}-{uuid4().hex[:8]}.db"
    partial = destination.with_suffix(".partial")
    # Online Backup API включает подтвержденные записи из WAL.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(partial)) as target:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RuntimeError("Проверка резервной копии не пройдена")
    partial.replace(destination)
    return destination


def schema_is_current(engine: Engine) -> bool:
    config = migration_config()
    heads = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
        return current == heads


def upgrade_database(path: Path) -> Path | None:
    existed = path.exists() and path.stat().st_size > 0
    engine = create_db_engine(path)
    try:
        if schema_is_current(engine):
            return None
        backup = backup_database(path) if existed else None
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        return backup
    finally:
        engine.dispose()
