"""Общие правила для будущих web-форм и Telegram; commit выполняет вызывающий код."""

import re
import unicodedata
from calendar import isleap
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import SYSTEM_GROUP_ID, Birthday, Delivery, Group


class DuplicateBirthdayError(ValueError):
    """Сохранение возможно только после явного подтверждения."""


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.split()))


def validate_birthday(name: str, day: int, month: int, year: int | None) -> str:
    name = normalize_text(name)
    if not 1 <= len(name) <= 200:
        raise ValueError("Имя должно содержать от 1 до 200 символов")
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if year is not None and not 1 <= year <= today.year:
        raise ValueError("Год рождения не может быть будущим")
    date(year if year is not None else 2000, month, day)
    return name


def create_birthday(
    session: Session,
    *,
    name: str,
    day: int,
    month: int,
    year: int | None = None,
    group_id: int = SYSTEM_GROUP_ID,
    note: str = "",
    confirm_duplicate: bool = False,
    birthday_id: int | None = None,
) -> Birthday:
    name = validate_birthday(name, day, month, year)
    if len(note) > 2000:
        raise ValueError("Заметка должна содержать не более 2000 символов")
    if session.get(Group, group_id) is None:
        raise ValueError("Группа не найдена")
    candidates = session.scalars(
        select(Birthday).where(
            Birthday.day == day,
            Birthday.month == month,
            Birthday.year == year,
        )
    )
    if not confirm_duplicate and any(
        item.id != birthday_id and normalize_text(item.name).casefold() == name.casefold()
        for item in candidates
    ):
        raise DuplicateBirthdayError("Такое имя и дата уже есть, подтвердите сохранение")
    birthday = session.get(Birthday, birthday_id) if birthday_id is not None else Birthday()
    if birthday is None:
        raise ValueError("Именинник не найден")
    date_changed = birthday_id is not None and (birthday.day, birthday.month) != (day, month)
    birthday.name, birthday.day, birthday.month = name, day, month
    birthday.year, birthday.group_id, birthday.note = year, group_id, note
    session.add(birthday)
    if date_changed:
        cancel_pending_deliveries(session, birthday_id)
    session.flush()
    return birthday


def create_group(
    session: Session, *, name: str, icon: str = "🏷", color: str = "#8A8178", sort_order: int = 0
) -> Group:
    name = normalize_text(name)
    if not 1 <= len(name) <= 100 or not 1 <= len(icon) <= 100:
        raise ValueError("Укажите название и иконку группы")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color) or sort_order < 0:
        raise ValueError("Укажите цвет #RRGGBB и неотрицательный порядок")
    group = Group(name=name, icon=icon, color=color, sort_order=sort_order)
    session.add(group)
    session.flush()
    return group


def delete_group(session: Session, group_id: int, *, transfer_to: int) -> None:
    if group_id == SYSTEM_GROUP_ID or group_id == transfer_to:
        raise ValueError("Системную группу нельзя удалить; выберите другую группу для переноса")
    group = session.get(Group, group_id)
    if group is None or session.get(Group, transfer_to) is None:
        raise ValueError("Группа не найдена")
    session.execute(
        update(Birthday).where(Birthday.group_id == group_id).values(group_id=transfer_to)
    )
    session.delete(group)
    session.flush()


def archive_birthday(session: Session, birthday_id: int) -> None:
    birthday = session.get(Birthday, birthday_id)
    if birthday is None:
        raise ValueError("Именинник не найден")
    birthday.is_active = False
    cancel_pending_deliveries(session, birthday_id)
    session.flush()


def occurrence_in_year(birthday: Birthday, year: int) -> date:
    day = 28 if birthday.month == 2 and birthday.day == 29 and not isleap(year) else birthday.day
    return date(year, birthday.month, day)


def age_in_year(birthday: Birthday, year: int) -> int | None:
    if birthday.year is None or year < birthday.year:
        return None
    return year - birthday.year


def cancel_pending_deliveries(session: Session, birthday_id: int) -> None:
    session.execute(
        update(Delivery)
        .where(
            Delivery.birthday_id == birthday_id,
            Delivery.status.in_(("pending", "retry", "sending")),
        )
        .values(status="cancelled")
    )


def restore_birthday(session: Session, birthday_id: int) -> None:
    birthday = session.get(Birthday, birthday_id)
    if birthday is None:
        raise ValueError("Именинник не найден")
    birthday.is_active = True
    session.flush()


def delete_birthday(session: Session, birthday_id: int) -> None:
    birthday = session.get(Birthday, birthday_id)
    if birthday is None or birthday.is_active:
        raise ValueError("Удалить окончательно можно только запись из архива")
    session.execute(delete(Delivery).where(Delivery.birthday_id == birthday_id))
    session.delete(birthday)
    session.flush()


def update_group(
    session: Session, group_id: int, *, name: str, icon: str, color: str, sort_order: int
) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise ValueError("Группа не найдена")
    name, icon = normalize_text(name), normalize_text(icon)
    if not 1 <= len(name) <= 100 or not 1 <= len(icon) <= 100:
        raise ValueError("Укажите название и иконку группы")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color) or not 0 <= sort_order <= 1_000_000:
        raise ValueError("Укажите цвет #RRGGBB и порядок от 0 до 1000000")
    if group.is_system and name != group.name:
        raise ValueError("Название системной группы нельзя изменить")
    group.name, group.icon, group.color, group.sort_order = name, icon, color, sort_order
    session.flush()
    return group
