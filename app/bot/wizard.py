"""Один черновик на привязку; запись и удаление черновика атомарны с offset."""

import json
import re
import secrets
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.auth.security import utc_now
from app.birthdays.service import (
    DuplicateBirthdayError,
    archive_birthday,
    create_birthday,
    normalize_text,
    validate_birthday,
)
from app.db.models import Birthday, BotDraft, Group

STEPS = ("name", "date", "year", "group", "note", "preview")
BACK = "↩️ Назад"
CANCEL = "✖️ Отмена"
KEEP = "Оставить как есть"
SKIP = "Пропустить"
KEYS = ("name", "day", "month", "year", "group_id", "note", "is_active")


def snapshot(person):
    return {key: getattr(person, key) for key in KEYS}


def keyboard(extra=()):
    return {
        "keyboard": [[{"text": value}] for value in extra] + [[{"text": BACK}, {"text": CANCEL}]],
        "resize_keyboard": True,
    }


def callback(draft, action):
    return f"wf:{draft.nonce}:{draft.version}:{action}"


def discard(db, user_id):
    db.execute(delete(BotDraft).where(BotDraft.user_id == user_id))


def start(db, user_id, telegram_id, *, birthday_id=None, mode="edit", quick=None):
    person = db.get(Birthday, birthday_id) if birthday_id is not None else None
    if birthday_id is not None and (person is None or not person.is_active):
        raise ValueError("Запись не найдена или уже в архиве.")
    discard(db, user_id)
    db.execute(delete(BotDraft).where(BotDraft.expires_at <= utc_now()))
    values = snapshot(person) if person else {"year": None, "group_id": 1, "note": ""}
    if quick:
        values.update(quick)
    data = {
        "values": values,
        "birthday_id": birthday_id,
        "original": snapshot(person) if person else None,
        "mode": mode,
        "duplicate": False,
    }
    draft = BotDraft(
        user_id=user_id,
        telegram_id=telegram_id,
        nonce=secrets.token_urlsafe(6),
        version=0,
        step="preview" if quick or mode == "archive" else "name",
        payload=json.dumps(data, ensure_ascii=False),
        expires_at=utc_now() + timedelta(minutes=15),
    )
    db.add(draft)
    db.flush()
    return draft


def prompt(db, draft):
    data = json.loads(draft.payload)
    values = data["values"]
    extra = [KEEP] if data["birthday_id"] else []
    if draft.step == "preview":
        group = db.get(Group, values["group_id"])
        date_label = f"{values['day']:02}.{values['month']:02}" + (
            f".{values['year']}" if values["year"] else " · год неизвестен"
        )
        text = (
            f"Проверьте запись:\n{values['name']}\n{date_label}\n"
            f"Группа: {group.name if group else 'Группа удалена'}\n"
            f"Заметка: {values['note'] or '—'}"
        )
        action, label = ("save", "Сохранить")
        if data["mode"] == "archive":
            text += "\n\nПереместить запись в архив? Ее можно восстановить в web."
            label = "В архив"
        elif data["duplicate"]:
            text += "\n\nТакое имя и дата уже есть, в том числе возможна запись в архиве."
            action, label = "duplicate", "Сохранить несмотря на дубль"
        return text, {
            "inline_keyboard": [
                [{"text": label, "callback_data": callback(draft, action)}],
                [
                    {"text": BACK, "callback_data": callback(draft, "back")},
                    {"text": CANCEL, "callback_data": callback(draft, "cancel")},
                ],
            ]
        }
    if draft.step == "name":
        text = "Как зовут именинника? Введите имя и фамилию."
    elif draft.step == "date":
        text = "Введите день и месяц: например, 17.05."
    elif draft.step == "year":
        text = "Укажите год рождения или пропустите, если он неизвестен."
        extra.append(SKIP)
    elif draft.step == "group":
        groups = list(db.scalars(select(Group).order_by(Group.sort_order, Group.id)))
        # Названия ограничены моделью; выдача страниц не зависит от длины клавиатуры.
        page = data.get("group_page", 0)
        page = min(page, max(0, (len(groups) - 1) // 20))
        text = "Введите номер группы:\n" + "\n".join(
            f"{g.id}. {g.name}" for g in groups[page * 20 : (page + 1) * 20]
        )
        extra.append("Без группы")
        if len(groups) > 20:
            extra.append("Другие группы")
    else:
        text = "Добавьте заметку (до 2000 символов) или пропустите."
        extra.append(SKIP)
    if data["birthday_id"]:
        current = {
            "name": values.get("name"),
            "date": f"{values.get('day')}.{values.get('month')}",
            "year": values.get("year") or "неизвестен",
            "group": values.get("group_id"),
            "note": values.get("note") or "—",
        }
        text += f"\nСейчас: {current.get(draft.step, '')}"
    return text, keyboard(extra)


def quick_values(text):
    match = re.fullmatch(r"(.+?)\s+(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", text.strip())
    if not match:
        return None
    name, day, month, year = match.groups()
    day, month, year = int(day), int(month), int(year) if year else None
    try:
        name = validate_birthday(name, day, month, year)
    except ValueError:
        raise ValueError("Проверьте имя и дату. Пример: Иван Петров 17.05.1985.") from None
    return {"name": name, "day": day, "month": month, "year": year}


def advance(db, draft, text=None, action=None):
    """Возвращает (текст, клавиатура, завершено). Ошибки не изменяют черновик."""
    data = json.loads(draft.payload)
    values = data["values"]
    if action == "cancel" or text in (CANCEL, "/cancel"):
        db.delete(draft)
        return "Действие отменено. Данные не изменены.", None, True
    if action == "back" or text == BACK:
        if data["mode"] == "archive":
            db.delete(draft)
            return "Архивирование отменено.", None, True
        draft.step = STEPS[max(0, STEPS.index(draft.step) - 1)]
        data["duplicate"] = False
    elif action in ("save", "duplicate"):
        if draft.step != "preview" or action == "duplicate" and not data["duplicate"]:
            raise ValueError("Эта кнопка уже не действует. Используйте последний предпросмотр.")
        if data["birthday_id"]:
            person = db.get(Birthday, data["birthday_id"])
            if person is None or snapshot(person) != data["original"]:
                db.delete(draft)
                return (
                    "Запись уже изменена или удалена. "
                    "Откройте ее заново — изменения не перезаписаны.",
                    None,
                    True,
                )
        try:
            if data["mode"] == "archive":
                archive_birthday(db, data["birthday_id"])
                result = "Запись перемещена в архив."
            else:
                person = create_birthday(
                    db,
                    birthday_id=data["birthday_id"],
                    name=values["name"],
                    day=values["day"],
                    month=values["month"],
                    year=values["year"],
                    group_id=values["group_id"],
                    note=values["note"],
                    confirm_duplicate=action == "duplicate",
                )
                result = f"День рождения сохранен: {person.name}."
        except DuplicateBirthdayError:
            data["duplicate"] = True
        except ValueError:
            raise ValueError(
                "Проверьте дату и группу. Вернитесь назад и исправьте данные."
            ) from None
        else:
            db.delete(draft)
            return result, None, True
    elif action is not None:
        raise ValueError("Неизвестное действие.")
    elif draft.step == "preview":
        raise ValueError("Используйте кнопку сохранения под последним предпросмотром.")
    elif text == "Другие группы" and draft.step == "group":
        groups = list(db.scalars(select(Group.id)))
        data["group_page"] = (data.get("group_page", 0) + 1) % max(1, (len(groups) + 19) // 20)
    else:
        keep = text == KEEP and data["birthday_id"] is not None
        if not keep:
            if draft.step == "name":
                name = normalize_text(text)
                if not 1 <= len(name) <= 200:
                    raise ValueError("Имя должно содержать от 1 до 200 символов.")
                values["name"] = name
            elif draft.step == "date":
                match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", text.strip())
                if not match:
                    raise ValueError("Введите дату в формате ДД.ММ.")
                day, month = map(int, match.groups())
                try:
                    date(2000, month, day)
                except ValueError:
                    raise ValueError("Такой даты нет в календаре.") from None
                values.update(day=day, month=month)
            elif draft.step == "year":
                try:
                    year = None if text == SKIP else int(text)
                    validate_birthday(values["name"], values["day"], values["month"], year)
                except ValueError:
                    raise ValueError("Проверьте год рождения и дату, включая 29 февраля.") from None
                values["year"] = year
            elif draft.step == "group":
                try:
                    group_id = 1 if text == "Без группы" else int(text)
                except ValueError:
                    raise ValueError("Введите номер группы из списка.") from None
                if db.get(Group, group_id) is None:
                    raise ValueError("Группа не найдена. Выберите другую.")
                values["group_id"] = group_id
            else:
                note = "" if text == SKIP else text
                if len(note) > 2000:
                    raise ValueError("Заметка должна быть не длиннее 2000 символов.")
                values["note"] = note
        draft.step = STEPS[STEPS.index(draft.step) + 1]
        data["duplicate"] = False
    draft.version += 1
    draft.payload = json.dumps(data, ensure_ascii=False)
    db.flush()
    message, markup = prompt(db, draft)
    return message, markup, False
