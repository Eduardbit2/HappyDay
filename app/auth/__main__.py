"""Первый администратор: пароль вводится скрыто и не передается в аргументах."""

import argparse
import sys
from getpass import getpass

from sqlalchemy.orm import Session

from app.auth.service import bootstrap_admin
from app.config import Settings
from app.db.engine import create_db_engine
from app.db.migrations import upgrade_database
from app.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description="Создание первого администратора HappyDay")
    parser.add_argument("action", choices=["create-admin"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--login", required=True)
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    if not sys.stdin.isatty():
        parser.exit(1, "Запустите команду в интерактивном терминале для скрытого ввода пароля.\n")
    password = getpass("Пароль (не менее 12 символов): ")
    if password != getpass("Повторите пароль: "):
        parser.exit(1, "Пароли не совпадают.\n")
    path = settings.data_dir / "happyday.db"
    upgrade_database(path)
    engine = create_db_engine(path)
    try:
        with Session(engine.execution_options(sqlite_write=True)) as db, db.begin():
            bootstrap_admin(db, name=args.name, login=args.login, password=password)
        print("Администратор создан. Откройте /login.")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
