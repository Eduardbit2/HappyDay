"""Сохраненный offset и изменения БД подтверждаются до отправки ответа."""

import asyncio
import logging
import re

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.bot.handler import handle_update
from app.db.models import BotCursor, TelegramLink, User
from app.notifications.engine import PermanentError, RetryableError

logger = logging.getLogger(__name__)


class BotRuntime:
    def __init__(self, api, db_engine, base_url):
        self.api, self.db_engine, self.base_url = api, db_engine, base_url
        self.username = None
        self.status = "connecting"

    def identify(self):
        bot = self.api.call("getMe")
        username = bot.get("username", "") if isinstance(bot, dict) else ""
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)
            or str(bot.get("id")) != self.api.bot_id
            or bot.get("is_bot") is not True
        ):
            raise PermanentError()
        self.username = username

    def poll_once(self, should_stop=lambda: False):
        with Session(self.db_engine) as db:
            cursor = db.get(BotCursor, self.api.bot_id)
            offset = cursor.next_update_id if cursor else 0
        updates = self.api.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 20,
                "limit": 50,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if not isinstance(updates, list):
            raise ValueError("Некорректный ответ Telegram")
        self.status = "ready"
        for update in updates:
            if should_stop():
                break
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            update_id = update["update_id"]
            if not 0 <= update_id < 2**63 - 1:
                continue
            with Session(self.db_engine.execution_options(sqlite_write=True)) as db, db.begin():
                cursor = db.get(BotCursor, self.api.bot_id)
                if cursor is None:
                    cursor = BotCursor(bot_id=self.api.bot_id, next_update_id=0)
                    db.add(cursor)
                if update_id < cursor.next_update_id:
                    continue
                replies = handle_update(db, update, self.base_url)
                cursor.next_update_id = update_id + 1
            callback_query = update.get("callback_query")
            if isinstance(callback_query, dict) and isinstance(callback_query.get("id"), str):
                try:
                    self.api.call(
                        "answerCallbackQuery", {"callback_query_id": callback_query["id"]}
                    )
                except Exception:
                    logger.warning("Подтверждение кнопки Telegram не доставлено")
            for reply in replies:
                if should_stop():
                    break
                if reply.user_id is not None:
                    with Session(self.db_engine) as db:
                        user = db.get(User, reply.user_id)
                        link = db.get(TelegramLink, reply.user_id)
                        if (
                            not user
                            or not user.is_active
                            or not link
                            or link.telegram_id != reply.chat_id
                        ):
                            break
                try:
                    self.api.send_text(reply.chat_id, reply.text, reply.markup)
                except Exception:
                    # Offset уже сохранен: повтор не должен заново предъявлять одноразовый код.
                    logger.warning("Ответ Telegram не подтвержден; запрос повторно не выполняется")
                    break


async def run_bot(runtime, notification_engine, stop):
    while not stop.is_set():
        delay = 1
        try:
            if runtime.username is None:
                await run_in_threadpool(runtime.identify)
            await run_in_threadpool(runtime.poll_once, stop.is_set)
            notification_engine.sender = runtime.api
        except RetryableError as error:
            runtime.status = "retry"
            delay = max(5, min(error.retry_after, 300))
        except PermanentError:
            runtime.status = "error"
            notification_engine.sender = None
            logger.warning(
                "Telegram недоступен: проверьте токен, webhook и единственный процесс бота"
            )
            delay = 30
        except Exception:
            runtime.status = "retry"
            logger.warning("Соединение с Telegram прервано; повтор подключения позже")
            delay = 10
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
