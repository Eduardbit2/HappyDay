"""Личные команды. Семейные данные доступны только активной подтвержденной привязке."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.birthdays.service import age_in_year, normalize_text, occurrence_in_year
from app.bot.linking import claim_pairing
from app.db.models import Birthday, Group, TelegramLink, User

MENU = {
    "keyboard": [
        [{"text": "📅 Ближайшие"}, {"text": "👥 Все"}],
        [{"text": "🎂 Добавить"}, {"text": "🏷 Группы"}],
        [{"text": "⚙️ Настройки"}],
    ],
    "resize_keyboard": True,
}


@dataclass
class Reply:
    chat_id: int
    text: str
    markup: dict | None = None
    user_id: int | None = None


def _handle_update(db, update, base_url):
    message = update.get("message")
    if not isinstance(message, dict):
        return []
    chat, sender = message.get("chat", {}), message.get("from", {})
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return []
    chat_id = chat.get("id")
    if (
        chat.get("type") != "private"
        or type(chat_id) is not int
        or not 0 < chat_id < 2**63
        or type(sender.get("id")) is not int
        or sender.get("id") != chat_id
        or sender.get("is_bot") is not False
    ):
        return []
    text = message.get("text", "")
    if not isinstance(text, str):
        return []
    if text.startswith("/start "):
        token = text.split(" ", 1)[1]
        name = normalize_text(
            str(sender.get("first_name", "")) + " " + str(sender.get("last_name", ""))
        )
        accepted = claim_pairing(db, token, chat_id, name)
        return [
            Reply(
                chat_id,
                "Теперь вернитесь в профиль HappyDay и подтвердите этот Telegram-аккаунт."
                if accepted
                else "Ссылка устарела или уже использована. Создайте новую в профиле HappyDay.",
            )
        ]
    user = db.scalar(
        select(User)
        .join(TelegramLink)
        .where(TelegramLink.telegram_id == chat_id, User.is_active.is_(True))
    )
    if user is None:
        return [
            Reply(
                chat_id,
                "Откройте профиль HappyDay в браузере и создайте ссылку подключения Telegram.",
                {"remove_keyboard": True},
            )
        ]
    if text in ("/start", "/menu"):
        return [Reply(chat_id, "HappyDay 🎂 Выберите действие.", MENU)]
    if text in ("/test", "Тест"):
        return [Reply(chat_id, "Telegram подключен. Тестовое сообщение HappyDay 🎂", MENU)]
    if text == "🎂 Добавить":
        return [
            Reply(
                chat_id,
                "Добавьте день рождения в HappyDay.",
                {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🌐 Добавить в браузере",
                                "url": base_url.rstrip("/") + "/birthdays/new",
                            }
                        ]
                    ]
                },
            )
        ]
    if text == "⚙️ Настройки":
        return [
            Reply(
                chat_id,
                "Подключение и отключение Telegram доступны в профиле.",
                {
                    "inline_keyboard": [
                        [{"text": "🌐 Профиль", "url": base_url.rstrip("/") + "/profile"}]
                    ]
                },
            )
        ]
    if text == "🏷 Группы":
        names = list(db.scalars(select(Group.name).order_by(Group.sort_order, Group.id).limit(20)))
        return [Reply(chat_id, "Группы:\n" + "\n".join(names), MENU)]
    query = "" if text in ("📅 Ближайшие", "👥 Все") else normalize_text(text).casefold()[:200]
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    events = []
    for person in db.scalars(select(Birthday).where(Birthday.is_active.is_(True))):
        if query and query not in person.name.casefold():
            continue
        occurrence = occurrence_in_year(person, today.year)
        if occurrence < today:
            occurrence = occurrence_in_year(person, today.year + 1)
        events.append((occurrence, person))
    events.sort(key=lambda event: (event[0], event[1].name.casefold(), event[1].id))
    if not events:
        return [Reply(chat_id, "Ничего не найдено. Можно искать по имени.", MENU)]
    replies = [
        Reply(
            chat_id,
            f"Найдено: {len(events)}. Показываю первые {min(5, len(events))}. "
            "Для поиска напишите имя.",
            MENU,
        )
    ]
    for occurrence, person in events[:5]:
        age = age_in_year(person, occurrence.year)
        label = f"{person.name}\n{occurrence:%d.%m.%Y}" + (
            f" · Возраст: {age}" if age is not None else ""
        )
        replies.append(
            Reply(
                chat_id,
                label,
                {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🌐 Открыть",
                                "url": f"{base_url.rstrip('/')}/birthdays/{person.id}",
                            }
                        ]
                    ]
                },
            )
        )
    return replies


def handle_update(db, update, base_url):
    replies = _handle_update(db, update, base_url)
    for reply in replies:
        reply.user_id = db.scalar(
            select(User.id)
            .join(TelegramLink)
            .where(TelegramLink.telegram_id == reply.chat_id, User.is_active.is_(True))
        )
    return replies
