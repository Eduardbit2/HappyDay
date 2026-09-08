"""Код запуска только предъявляет Telegram; доступ дает подтверждение в web."""

from datetime import timedelta

from sqlalchemy import delete, select, update

from app.auth.security import new_token, token_hash, utc_now
from app.db.models import AuthSession, Delivery, TelegramLink, TelegramPairRequest, User


def issue_pairing(db, user_id, session_hash):
    if db.get(TelegramLink, user_id):
        raise ValueError("Сначала отключите текущую привязку")
    db.execute(
        delete(TelegramPairRequest).where(
            (TelegramPairRequest.user_id == user_id) | (TelegramPairRequest.expires_at <= utc_now())
        )
    )
    token = new_token()
    db.add(
        TelegramPairRequest(
            user_id=user_id,
            session_hash=session_hash,
            token_hash=token_hash(token),
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    db.flush()
    return token


def claim_pairing(db, token, telegram_id, display_name):
    if len(token) != 43 or type(telegram_id) is not int or not 0 < telegram_id < 2**63:
        return False
    request = db.scalar(
        select(TelegramPairRequest).where(TelegramPairRequest.token_hash == token_hash(token))
    )
    if not request or request.expires_at <= utc_now() or request.telegram_id is not None:
        return False
    user = db.get(User, request.user_id)
    session = db.get(AuthSession, request.session_hash)
    if (
        not user
        or not user.is_active
        or not session
        or session.expires_at <= utc_now()
        or session.user_id != request.user_id
    ):
        return False
    if db.get(TelegramLink, user.id) or db.scalar(
        select(TelegramLink).where(TelegramLink.telegram_id == telegram_id)
    ):
        return False
    request.telegram_id = telegram_id
    request.display_name = display_name[:200]
    db.flush()
    return True


def confirm_pairing(db, user_id, request_hash, telegram_id):
    request = db.get(TelegramPairRequest, user_id)
    if not request or request.expires_at <= utc_now() or not request.telegram_id:
        raise ValueError("Запрос устарел. Создайте новую ссылку и откройте ее в Telegram.")
    session = db.get(AuthSession, request.session_hash)
    user = db.get(User, user_id)
    if (
        request.token_hash != request_hash
        or str(request.telegram_id) != telegram_id
        or not session
        or session.expires_at <= utc_now()
        or session.user_id != user_id
        or not user
        or not user.is_active
    ):
        raise ValueError("Запрос изменился или устарел. Обновите страницу.")
    if db.get(TelegramLink, user_id) or db.scalar(
        select(TelegramLink).where(TelegramLink.telegram_id == request.telegram_id)
    ):
        raise ValueError("Аккаунт уже привязан. Создайте новый запрос после отключения.")
    db.add(TelegramLink(user_id=user_id, telegram_id=request.telegram_id))
    db.delete(request)
    db.flush()


def unlink(db, user_id):
    db.execute(delete(TelegramPairRequest).where(TelegramPairRequest.user_id == user_id))
    db.execute(delete(TelegramLink).where(TelegramLink.user_id == user_id))
    db.execute(
        update(Delivery)
        .where(Delivery.user_id == user_id, Delivery.status.in_(("pending", "retry")))
        .values(status="cancelled", last_error="Telegram отключен")
    )
