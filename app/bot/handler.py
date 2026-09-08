"""Личные команды. Семейные данные доступны только активной подтвержденной привязке."""

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.auth.security import utc_now
from app.birthdays.service import age_in_year, normalize_text, occurrence_in_year
from app.bot import wizard
from app.bot.linking import claim_pairing
from app.db.models import Birthday, BotDraft, Group, TelegramLink, User

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
    callback_query = update.get("callback_query")
    is_callback = isinstance(callback_query, dict)
    callback_data = callback_query.get("data", "") if is_callback else None
    if is_callback and (
        not isinstance(callback_data, str) or len(callback_data.encode("utf-8")) > 64
    ):
        return []
    message = callback_query.get("message") if is_callback else update.get("message")
    if not isinstance(message, dict):
        return []
    chat = message.get("chat", {})
    sender = callback_query.get("from", {}) if is_callback else message.get("from", {})
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
    text = "" if is_callback else message.get("text", "")
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
    if text in ("/start", "/menu", "/cancel", wizard.CANCEL):
        wizard.discard(db, user.id)
        return [
            Reply(
                chat_id,
                "Действие отменено. Выберите действие."
                if text in ("/cancel", wizard.CANCEL)
                else "HappyDay 🎂 Выберите действие.",
                MENU,
            )
        ]
    draft = db.get(BotDraft, user.id)
    expired = bool(draft and (draft.expires_at <= utc_now() or draft.telegram_id != chat_id))
    if expired:
        db.delete(draft)
        db.flush()
        draft = None
    try:
        if is_callback:
            match = re.fullmatch(r"(edit|archive):([1-9][0-9]{0,17})", callback_data)
            if match:
                if draft:
                    raise ValueError("Сначала завершите черновик или отправьте /cancel.")
                mode, birthday_id = match.groups()
                draft = wizard.start(db, user.id, chat_id, birthday_id=int(birthday_id), mode=mode)
                message, markup = wizard.prompt(db, draft)
                return [Reply(chat_id, message, markup)]
            match = re.fullmatch(
                r"wf:([A-Za-z0-9_-]{8}):([0-9]{1,9}):(save|duplicate|back|cancel)", callback_data
            )
            if not match or not draft or match[1] != draft.nonce or int(match[2]) != draft.version:
                return [
                    Reply(
                        chat_id,
                        "Кнопка устарела. Используйте последний шаг или начните заново.",
                        MENU if not draft else None,
                    )
                ]
            message, markup, done = wizard.advance(db, draft, action=match[3])
            return [Reply(chat_id, message, MENU if done else markup)]
        if text in ("🎂 Добавить", "/add"):
            draft = draft or wizard.start(db, user.id, chat_id)
            message, markup = wizard.prompt(db, draft)
            return [Reply(chat_id, message, markup)]
        if expired:
            return [
                Reply(
                    chat_id,
                    "Черновик истек через 15 минут. Начните заново через «🎂 Добавить».",
                    MENU,
                )
            ]
        if draft:
            if text in ("📅 Ближайшие", "👥 Все", "🏷 Группы", "⚙️ Настройки", "/test"):
                raise ValueError("Сначала завершите черновик или отправьте /cancel.")
            if not text:
                message, markup = wizard.prompt(db, draft)
                return [Reply(chat_id, "На этом шаге нужен текст.\n" + message, markup)]
            message, markup, done = wizard.advance(db, draft, text=text)
            return [Reply(chat_id, message, MENU if done else markup)]
        quick = wizard.quick_values(text)
        if quick:
            draft = wizard.start(db, user.id, chat_id, quick=quick)
            message, markup = wizard.prompt(db, draft)
            return [Reply(chat_id, message, markup)]
    except ValueError as error:
        if draft:
            message, markup = wizard.prompt(db, draft)
            return [Reply(chat_id, str(error) + "\n\n" + message, markup)]
        return [Reply(chat_id, str(error), MENU)]
    if text in ("/test", "Тест"):
        return [Reply(chat_id, "Telegram подключен. Тестовое сообщение HappyDay 🎂", MENU)]
    if not text:
        return [Reply(chat_id, "Для поиска напишите имя или выберите действие.", MENU)]
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
                            {"text": "✏️ Изменить", "callback_data": f"edit:{person.id}"},
                            {"text": "🗑 В архив", "callback_data": f"archive:{person.id}"},
                        ],
                        [
                            {
                                "text": "🌐 Открыть",
                                "url": f"{base_url.rstrip('/')}/birthdays/{person.id}",
                            }
                        ],
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
