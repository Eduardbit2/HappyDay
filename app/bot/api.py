"""Bot API без передачи секретов в исключения и журналы."""

import logging

import httpx
from pydantic import SecretStr

from app.notifications.engine import Message, PermanentError, RetryableError, UnknownResultError


class TelegramAPI:
    def __init__(self, token: SecretStr, *, transport=None):
        self.bot_id = token.get_secret_value().split(":")[0]
        self._base = "https://api.telegram.org/bot" + token.get_secret_value() + "/"
        self._client = httpx.Client(
            timeout=httpx.Timeout(25, connect=5),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        # httpx INFO содержит URL с токеном; httpcore DEBUG может содержать тело.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    def close(self):
        self._client.close()

    def call(self, method: str, payload: dict | None = None):
        if method not in {"getMe", "getUpdates", "sendMessage", "answerCallbackQuery"}:
            raise ValueError("Неподдерживаемый метод Telegram")
        try:
            response = self._client.post(self._base + method, json=payload or {})
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise UnknownResultError() from None
        if not isinstance(body, dict):
            raise UnknownResultError()
        if body.get("ok") is True and response.status_code == 200 and "result" in body:
            return body["result"]
        code = body.get("error_code", response.status_code)
        if code == 429:
            parameters = body.get("parameters")
            delay = parameters.get("retry_after", 60) if isinstance(parameters, dict) else 60
            raise RetryableError(retry_after=delay if type(delay) is int else 60)
        if code in (400, 401, 403, 404, 409):
            raise PermanentError()
        # 5xx/невалидный ответ могут последовать за уже принятой отправкой.
        raise UnknownResultError()

    def send_text(self, chat_id: int, text: str, markup=None):
        if type(chat_id) is not int or not 0 < chat_id < 2**63:
            raise ValueError("Разрешены только личные чаты")
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
            "link_preview_options": {"is_disabled": True},
        }
        if markup:
            payload["reply_markup"] = markup
        result = self.call("sendMessage", payload)
        if not isinstance(result, dict) or type(result.get("message_id")) is not int:
            raise UnknownResultError()
        return result

    def send(self, message: Message):
        self.send_text(
            message.telegram_id,
            message.text,
            {"inline_keyboard": [[{"text": "🌐 Открыть", "url": message.url}]]},
        )
