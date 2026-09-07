"""Общие web-зависимости: cookie, серверная сессия, CSRF и ограниченные формы."""

import secrets
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.auth.security import token_hash, utc_now
from app.auth.service import create_session
from app.db.models import AuthSession, User

templates = Jinja2Templates(directory=str(Path(__file__).parents[2] / "templates"))
templates.env.filters["moscow_time"] = lambda value: (
    value.replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
)
COOKIE_NAME = "happyday_session"


@dataclass
class AuthContext:
    db: Session
    session: AuthSession
    user: User | None
    new_cookie: str | None = None


def get_db(request: Request):
    # Сериализация коротких auth-транзакций защищает одноразовые приглашения и rate limits.
    with (
        Session(request.app.state.db_engine.execution_options(sqlite_write=True)) as db,
        db.begin(),
    ):
        yield db


def get_context(
    request: Request, db: Annotated[Session, Depends(get_db, scope="function")]
) -> AuthContext:
    cookie = request.cookies.get(COOKIE_NAME, "")
    session = db.get(AuthSession, token_hash(cookie)) if len(cookie) == 43 else None
    user = db.get(User, session.user_id) if session and session.user_id else None
    if session and (
        session.expires_at <= utc_now() or (session.user_id and (not user or not user.is_active))
    ):
        db.delete(session)
        db.flush()
        session, user = None, None
    if session is None:
        session, token = create_session(db)
        return AuthContext(db, session, None, token)
    return AuthContext(db, session, user)


def current_user(ctx: Annotated[AuthContext, Depends(get_context)]) -> User:
    if ctx.user is None:
        raise HTTPException(303, headers={"Location": "/login"})
    return ctx.user


def administrator(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Доступ только для администратора")
    return user


def origin_of(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


async def protected_form(
    request: Request, ctx: Annotated[AuthContext, Depends(get_context)]
) -> dict[str, str]:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return {}
    settings = request.app.state.settings
    allowed = {origin_of(str(settings.base_url))}
    if settings.env != "production":
        allowed.add(origin_of(str(request.base_url)))
    source = request.headers.get("origin") or request.headers.get("referer", "")
    try:
        origin = origin_of(source)
    except ValueError:
        origin = ""
    if not origin or origin not in allowed:
        raise HTTPException(403, "Источник запроса не разрешен")
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Ожидается обычная web-форма")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_384:
            raise HTTPException(413, "Форма слишком большая")
    try:
        parsed = parse_qs(
            body.decode("utf-8"), keep_blank_values=True, max_num_fields=20, errors="strict"
        )
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Некорректная форма") from None
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(400, "Поля формы не должны повторяться")
    fields = {key: values[0] for key, values in parsed.items()}
    if not secrets.compare_digest(
        fields.get("csrf_token", "").encode(), ctx.session.csrf_token.encode()
    ):
        raise HTTPException(403, "Форма устарела. Обновите страницу")
    return fields


def attach_cookie(response, request: Request, ctx: AuthContext):
    if ctx.new_cookie:
        seconds = max(0, int((ctx.session.expires_at - utc_now()).total_seconds()))
        response.set_cookie(
            COOKIE_NAME,
            ctx.new_cookie,
            max_age=seconds,
            httponly=True,
            secure=request.app.state.settings.env == "production"
            or request.app.state.settings.base_url.scheme == "https",
            samesite="lax",
            path="/",
        )
    return response


def render(request: Request, ctx: AuthContext, template: str, *, status: int = 200, **values):
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "user": ctx.user,
            "csrf": ctx.session.csrf_token,
            "error": None,
            "now": utc_now(),
            **values,
        },
        status_code=status,
    )
    return attach_cookie(response, request, ctx)


def redirect(request: Request, ctx: AuthContext, path: str):
    return attach_cookie(RedirectResponse(path, status_code=303), request, ctx)
