"""python -m app.db upgrade | check | backup"""

import argparse

from alembic import command

from app.config import Settings
from app.db.engine import create_db_engine
from app.db.migrations import backup_database, migration_config, schema_is_current, upgrade_database
from app.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description="Миграции и backup HappyDay")
    parser.add_argument("action", choices=["upgrade", "check", "backup"])
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    path = settings.data_dir / "happyday.db"
    if args.action == "upgrade":
        backup = upgrade_database(path)
        print("Схема актуальна." + (f" Backup: {backup}" if backup else ""))
    elif args.action == "backup":
        print(backup_database(path))
    else:
        if not path.is_file():
            parser.exit(1, "База еще не создана. Выполните upgrade.\n")
        engine = create_db_engine(path)
        try:
            if not schema_is_current(engine):
                parser.exit(1, "Схема не актуальна. Выполните upgrade.\n")
            with engine.begin() as connection:
                config = migration_config()
                config.attributes["connection"] = connection
                command.check(config)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
