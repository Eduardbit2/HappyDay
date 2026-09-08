"""Личный web-профиль Telegram. Все изменения защищены общей CSRF-зависимостью."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from app.auth.security import utc_now
from app.auth.web import current_user, protected_form, redirect, render
from app.bot.linking import confirm_pairing, issue_pairing, unlink
from app.db.models import TelegramLink, TelegramPairRequest, User
from app.web.birthdays import Context, Fields

router = APIRouter(dependencies=[Depends(current_user), Depends(protected_form)])
logger = logging.getLogger(__name__)


def telegram_page(request, ctx, *, error=None, token=None, notice=None):
    runtime = request.app.state.telegram_runtime
    pairing = ctx.db.get(TelegramPairRequest, ctx.user.id)
    if pairing and pairing.expires_at <= utc_now():
        pairing = None
    username = runtime.username if runtime else None
    return render(
        request,
        ctx,
        "telegram.html",
        active="settings",
        hide_add=True,
        error=error,
        status=400 if error else 200,
        pairing=pairing,
        link=ctx.db.get(TelegramLink, ctx.user.id),
        username=username,
        bot_status=runtime.status if runtime else "disabled",
        pairing_url=f"https://t.me/{username}?start={token}" if username and token else None,
        notice=notice,
    )


@router.get("/profile/telegram")
def telegram_profile(request: Request, ctx: Context, test_requested: bool = False):
    return telegram_page(
        request,
        ctx,
        notice="Тестовое сообщение запрошено. Проверьте личный чат с ботом."
        if test_requested
        else None,
    )


@router.post("/profile/telegram/link")
def create_link(request: Request, ctx: Context, fields: Fields):
    runtime = request.app.state.telegram_runtime
    if not runtime or not runtime.username or runtime.status != "ready":
        return telegram_page(
            request, ctx, error="Бот пока недоступен. Обратитесь к администратору."
        )
    try:
        token = issue_pairing(ctx.db, ctx.user.id, ctx.session.token_hash)
    except ValueError as error:
        return telegram_page(request, ctx, error=str(error))
    return telegram_page(request, ctx, token=token)


@router.post("/profile/telegram/confirm")
def confirm_link(request: Request, ctx: Context, fields: Fields):
    try:
        confirm_pairing(
            ctx.db, ctx.user.id, fields.get("request_hash", ""), fields.get("telegram_id", "")
        )
    except ValueError as error:
        return telegram_page(request, ctx, error=str(error))
    return redirect(request, ctx, "/profile/telegram")


@router.post("/profile/telegram/unlink")
def remove_link(request: Request, ctx: Context, fields: Fields):
    if fields.get("confirm") != "1":
        return telegram_page(request, ctx, error="Подтвердите отключение Telegram.")
    unlink(ctx.db, ctx.user.id)
    return redirect(request, ctx, "/profile/telegram")


def send_test(db_engine, api, user_id):
    with Session(db_engine) as db:
        user, link = db.get(User, user_id), db.get(TelegramLink, user_id)
        recipient = link.telegram_id if user and user.is_active and link else None
    if recipient:
        try:
            api.send_text(
                recipient, "HappyDay 🎂 Тестовое сообщение. Личные напоминания подключены."
            )
        except Exception:
            logger.warning("Тестовое сообщение Telegram не подтверждено")


@router.post("/profile/telegram/test")
def test_link(request: Request, ctx: Context, fields: Fields, background: BackgroundTasks):
    runtime = request.app.state.telegram_runtime
    if not runtime or runtime.status != "ready" or not ctx.db.get(TelegramLink, ctx.user.id):
        return telegram_page(request, ctx, error="Для теста нужен подключенный Telegram.")
    # BackgroundTasks запускается после commit зависимости scope=function.
    background.add_task(send_test, request.app.state.db_engine, runtime.api, ctx.user.id)
    response = redirect(request, ctx, "/profile/telegram?test_requested=1")
    response.background = background
    return response
