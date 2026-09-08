import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.birthdays.service import (
    DuplicateBirthdayError,
    age_in_year,
    archive_birthday,
    create_birthday,
    create_group,
    delete_group,
    occurrence_in_year,
)
from app.db.engine import create_db_engine
from app.db.migrations import backup_database, migration_config, schema_is_current, upgrade_database
from app.db.models import Birthday, Delivery, DeliveryAttempt, Group, TelegramLink, User


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "данные-Ёжики-🎂" / "happyday.db"
    upgrade_database(path)
    engine = create_db_engine(path)
    yield engine, path
    engine.dispose()


def test_migration_restart_and_metadata(database):
    engine, path = database
    assert schema_is_current(engine)
    assert upgrade_database(path) is None
    assert not (path.parent / "backups").exists()
    with engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.check(config)
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert (
            connection.execute(text("SELECT name FROM groups WHERE id=1")).scalar() == "Без группы"
        )


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM groups WHERE id=1",
        "UPDATE groups SET name='Другие' WHERE id=1",
        "UPDATE groups SET id=10, is_system=0 WHERE id=1",
        "INSERT INTO groups(id,name,is_system) VALUES(2,'Система',1)",
        "INSERT INTO birthdays(name,day,month,group_id) VALUES('Иван',1,1,999)",
        "INSERT INTO birthdays(name,day,month) VALUES('Иван',31,4)",
        "INSERT INTO birthdays(name,day,month,year) VALUES('Иван',29,2,1900)",
        "INSERT INTO birthdays(name,day,month,year) VALUES('Иван',1,1,9999)",
    ],
)
def test_sql_constraints(database, statement):
    engine, _ = database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(statement))


def test_birthdays_groups_archive_and_utf8(database):
    engine, _ = database
    with Session(engine) as session, session.begin():
        group = create_group(session, name="Семья", icon="🎂")
        person = create_birthday(
            session,
            name="  Алёна   Ёжик ",
            day=29,
            month=2,
            group_id=group.id,
            note="Любит торт 🎂",
        )
        assert person.name == "Алёна Ёжик"
        assert age_in_year(person, 2026) is None
        assert occurrence_in_year(person, 2026) == date(2026, 2, 28)
        assert occurrence_in_year(person, 2028) == date(2028, 2, 29)
        with pytest.raises(DuplicateBirthdayError):
            create_birthday(session, name="алёна ёжик", day=29, month=2)
        duplicate = create_birthday(
            session, name="Алёна Ёжик", day=29, month=2, confirm_duplicate=True
        )
        archive_birthday(session, duplicate.id)
        delete_group(session, group.id, transfer_to=1)
        person_id = person.id
    with Session(engine) as session:
        person = session.get(Birthday, person_id)
        assert person.note == "Любит торт 🎂"
        assert person.group_id == 1
        assert len(list(session.scalars(select(Group)))) == 1
        assert (
            len(list(session.scalars(select(Birthday).where(Birthday.is_active.is_(False))))) == 1
        )


@pytest.mark.parametrize(
    "day,month,year",
    [(31, 4, None), (29, 2, 1900), (0, 1, None), (1, 13, None), (1, 1, 9999), (1, 1, 0)],
)
def test_invalid_dates(database, day, month, year):
    engine, _ = database
    with Session(engine) as session, session.begin(), pytest.raises(ValueError):
        create_birthday(session, name="Иван", day=day, month=month, year=year)


def test_known_year_and_group_rollback(database):
    engine, _ = database
    with Session(engine) as session, session.begin():
        group = create_group(session, name="Друзья")
        group_id = group.id
        person = create_birthday(
            session, name="Пётр", day=29, month=2, year=2000, group_id=group_id
        )
        assert age_in_year(person, 2026) == 26
    with pytest.raises(RuntimeError), Session(engine) as session, session.begin():
        delete_group(session, group_id, transfer_to=1)
        raise RuntimeError("Отмена транзакции")
    with Session(engine) as session:
        assert session.get(Group, group_id) is not None
        assert session.scalar(select(Birthday.group_id)) == group_id
        with pytest.raises(ValueError):
            delete_group(session, 1, transfer_to=group_id)
        with pytest.raises(ValueError):
            delete_group(session, group_id, transfer_to=999)


def test_telegram_and_delivery_uniqueness(database):
    engine, _ = database
    with Session(engine) as session, session.begin():
        user = User(name="Администратор", login="admin", password_hash="test-hash", role="admin")
        session.add(user)
        session.flush()
        person = create_birthday(session, name="Иван", day=1, month=1)
        session.add(TelegramLink(user_id=user.id, telegram_id=1234567890123))
        user_id, person_id = user.id, person.id
    values = dict(
        birthday_id=person_id,
        user_id=user_id,
        occurrence_date=date(2027, 1, 1),
        days_before=7,
        next_attempt_at=datetime(2026, 12, 25, 6),
    )
    with Session(engine) as session, session.begin():
        session.add(Delivery(**values))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(Delivery(**values))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        other = User(name="Другой", login="other", password_hash="test-hash")
        session.add(other)
        session.flush()
        session.add(TelegramLink(user_id=other.id, telegram_id=1234567890123))
    with Session(engine) as session, session.begin():
        session.add(Delivery(**{**values, "days_before": 1}))
        session.delete(session.get(TelegramLink, user_id))
    with Session(engine) as session:
        assert session.get(User, user_id).is_active
        assert session.get(TelegramLink, user_id) is None


def test_backup_wal_and_restore(database, tmp_path):
    engine, path = database
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO birthdays(name,day,month,note) VALUES('Ёжик',1,1,'🎂')")
        )
    backup = backup_database(path)
    restored = tmp_path / "restored.db"
    restored.write_bytes(backup.read_bytes())
    restored_engine = create_db_engine(restored)
    try:
        assert schema_is_current(restored_engine)
        with restored_engine.connect() as connection:
            assert connection.execute(text("SELECT note FROM birthdays")).scalar() == "🎂"
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    finally:
        restored_engine.dispose()


def test_backup_before_upgrading_existing_database(tmp_path):
    path = tmp_path / "happyday.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_note (content TEXT)")
        connection.execute("INSERT INTO legacy_note VALUES ('До миграции')")
    backup = upgrade_database(path)
    assert isinstance(backup, Path)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT content FROM legacy_note").fetchone() == ("До миграции",)
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
            is None
        )


def test_downgrade_and_upgrade(database):
    engine, path = database
    with engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()
    backup = upgrade_database(path)
    assert backup is not None
    assert schema_is_current(engine)


def test_transactional_ddl(database):
    engine, _ = database
    with pytest.raises(RuntimeError), engine.begin() as connection:
        connection.execute(text("CREATE TABLE must_rollback (id INTEGER)"))
        raise RuntimeError("Ошибка миграции")
    assert "must_rollback" not in inspect(engine).get_table_names()


def test_backup_failure_prevents_migration(tmp_path, monkeypatch):
    path = tmp_path / "happyday.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE original (id INTEGER)")

    def fail_backup(_path):
        raise OSError("Нет места для backup")

    monkeypatch.setattr("app.db.migrations.backup_database", fail_backup)
    with pytest.raises(OSError):
        upgrade_database(path)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
            is None
        )


def test_attempt_history_and_sent_invariant(database):
    engine, _ = database
    with Session(engine) as session, session.begin():
        user = User(name="Тест", login="test", password_hash="test-hash")
        session.add(user)
        session.flush()
        person = create_birthday(session, name="Ёжик", day=1, month=1)
        delivery = Delivery(
            birthday_id=person.id,
            user_id=user.id,
            occurrence_date=date(2027, 1, 1),
            days_before=0,
            next_attempt_at=datetime(2027, 1, 1, 6),
        )
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id
        session.add(
            DeliveryAttempt(
                delivery_id=delivery_id, attempt_number=1, outcome="retry", error="Временная ошибка"
            )
        )
        session.add(DeliveryAttempt(delivery_id=delivery_id, attempt_number=2, outcome="sent"))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(DeliveryAttempt(delivery_id=delivery_id, attempt_number=1, outcome="failed"))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.get(Delivery, delivery_id).status = "sent"
    with Session(engine) as session, session.begin():
        delivery = session.get(Delivery, delivery_id)
        delivery.status = "sent"
        delivery.sent_at = datetime(2027, 1, 1, 6, 1)
        delivery.attempts = 2
    with Session(engine) as session:
        history = list(
            session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        )
        assert [attempt.outcome for attempt in history] == ["retry", "sent"]
        assert history[0].error == "Временная ошибка"


def test_failed_upgrade_rolls_back_and_preserves_backup(tmp_path, monkeypatch):
    path = tmp_path / "happyday.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE original (id INTEGER)")
        connection.execute("INSERT INTO original VALUES (42)")

    def broken_upgrade(config, _revision):
        config.attributes["connection"].execute(text("CREATE TABLE interrupted (id INTEGER)"))
        raise RuntimeError("Migration interrupted")

    monkeypatch.setattr("app.db.migrations.command.upgrade", broken_upgrade)
    with pytest.raises(RuntimeError):
        upgrade_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id FROM original").fetchone() == (42,)
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='interrupted'").fetchone()
            is None
        )
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_csv_migration_preserves_existing_data_and_backup(tmp_path):
    path = tmp_path / "happyday.db"
    engine = create_db_engine(path)
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "0002_auth")
            connection.exec_driver_sql(
                "INSERT INTO users (name, login, password_hash, role) VALUES (?, ?, ?, ?)",
                ("Администратор Ёжик", "admin", "test-hash", "admin"),
            )
            connection.exec_driver_sql(
                "INSERT INTO birthdays (name, day, month) VALUES ('Лёля 🎂', 29, 2)"
            )
        backup = upgrade_database(path)
        assert backup is not None
        with sqlite3.connect(backup) as snapshot:
            assert (
                snapshot.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "0002_auth"
            )
            assert snapshot.execute("SELECT name FROM users").fetchone()[0] == "Администратор Ёжик"
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            command.downgrade(config, "0002_auth")
            assert connection.exec_driver_sql("SELECT name FROM birthdays").scalar() == "Лёля 🎂"
            assert connection.exec_driver_sql("SELECT login FROM users").scalar() == "admin"
    finally:
        engine.dispose()


def test_telegram_migration_preserves_links(tmp_path):
    path = tmp_path / "happyday.db"
    engine = create_db_engine(path)
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "0003_csv_drafts")
            connection.exec_driver_sql(
                "INSERT INTO users (name, login, password_hash) VALUES ('Алёна', 'admin', 'test')"
            )
            connection.exec_driver_sql(
                "INSERT INTO telegram_links (user_id, telegram_id) VALUES (1, 54321)"
            )
        backup = upgrade_database(path)
        assert backup is not None
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            command.downgrade(config, "0003_csv_drafts")
            assert (
                connection.exec_driver_sql("SELECT telegram_id FROM telegram_links").scalar()
                == 54321
            )
            assert connection.exec_driver_sql("SELECT name FROM users").scalar() == "Алёна"
    finally:
        engine.dispose()
