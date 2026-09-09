"""Проверка head и соответствия ORM на отдельной временной SQLite."""

from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command

from app.db.engine import create_db_engine
from app.db.migrations import migration_config, schema_is_current, upgrade_database


def main():
    with TemporaryDirectory(prefix="happyday-migrations-") as directory:
        path = Path(directory) / "данные-Ёжики-🎂" / "happyday.db"
        upgrade_database(path)
        engine = create_db_engine(path)
        try:
            assert schema_is_current(engine)
            with engine.begin() as connection:
                config = migration_config()
                config.attributes["connection"] = connection
                command.check(config)
            assert upgrade_database(path) is None
        finally:
            engine.dispose()
    print("Migrations and ORM: OK")


if __name__ == "__main__":
    main()
