from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth import service
from app.auth.web import (
    COOKIE_NAME,
    AuthContext,
    administrator,
    current_user,
    get_context,
    protected_form,
    redirect,
    render,
)
from app.db.models import Invitation, User

router = APIRouter(dependencies=[Depends(protected_form)])


@router.get("/login")
def login_page(request: Request, ctx: Annotated[AuthContext, Depends(get_context)]):
    if ctx.user:
        return redirect(request, ctx, "/")
    return render(request, ctx, "login.html")


@router.post("/login")
def login(
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        user = service.authenticate(
            ctx.db,
            fields.get("login", ""),
            fields.get("password", ""),
            request.client.host if request.client else "unknown",
        )
    except service.LoginLimited as error:
        response = render(request, ctx, "login.html", status=429, error=str(error))
        response.headers["Retry-After"] = "900"
        return response
    if not user:
        return render(request, ctx, "login.html", status=400, error="Неверный логин или пароль")
    ctx.session, ctx.new_cookie = service.rotate_session(
        ctx.db, ctx.session, user, request.app.state.settings.session_hours
    )
    ctx.user = user
    return redirect(request, ctx, "/")


@router.get("/")
def home(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    return render(request, ctx, "home.html")


@router.get("/profile")
def profile(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    return render(request, ctx, "profile.html")


@router.post("/logout")
def logout(
    user: Annotated[User, Depends(current_user)], ctx: Annotated[AuthContext, Depends(get_context)]
):
    ctx.db.delete(ctx.session)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/logout-all")
def logout_all(
    user: Annotated[User, Depends(current_user)], ctx: Annotated[AuthContext, Depends(get_context)]
):
    service.revoke_user_sessions(ctx.db, user.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/profile/password")
def password_change(
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        service.throttle_login(
            ctx.db, user.login, request.client.host if request.client else "unknown"
        )
        if fields.get("password") != fields.get("password_confirm"):
            raise ValueError("Пароли не совпадают")
        service.change_password(
            ctx.db, user, fields.get("current_password", ""), fields.get("password", "")
        )
    except ValueError as error:
        return render(
            request,
            ctx,
            "profile.html",
            status=429 if isinstance(error, service.LoginLimited) else 400,
            error=str(error),
        )
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/join")
def join_page(request: Request, ctx: Annotated[AuthContext, Depends(get_context)]):
    if ctx.user:
        return redirect(request, ctx, "/")
    return render(request, ctx, "join.html")


@router.post("/join")
def join(
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    if ctx.user:
        return redirect(request, ctx, "/")
    try:
        service.throttle_login(
            ctx.db,
            "invite:" + (request.client.host if request.client else "unknown"),
            request.client.host if request.client else "unknown",
        )
        if fields.get("password") != fields.get("password_confirm"):
            raise ValueError("Пароли не совпадают")
        user = service.accept_invitation(
            ctx.db,
            token=fields.get("token", ""),
            name=fields.get("name", ""),
            login=fields.get("login", ""),
            password=fields.get("password", ""),
        )
    except ValueError as error:
        return render(
            request,
            ctx,
            "join.html",
            status=429 if isinstance(error, service.LoginLimited) else 400,
            error=str(error),
        )
    ctx.session, ctx.new_cookie = service.rotate_session(
        ctx.db, ctx.session, user, request.app.state.settings.session_hours
    )
    ctx.user = user
    return redirect(request, ctx, "/")


def admin_page(request, ctx, *, status=200, **values):
    users = list(ctx.db.scalars(select(User).order_by(User.id)))
    invites = list(ctx.db.scalars(select(Invitation).order_by(Invitation.id.desc()).limit(30)))
    return render(
        request, ctx, "admin.html", status=status, users=users, invitations=invites, **values
    )


@router.get("/admin")
def admin(
    request: Request,
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    return admin_page(request, ctx)


@router.post("/admin/invitations")
def invite(
    request: Request,
    actor: Annotated[User, Depends(administrator)],
    fields: Annotated[dict, Depends(protected_form)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        _invite, token = service.create_invitation(ctx.db, actor, role=fields.get("role", "member"))
    except ValueError as error:
        return admin_page(request, ctx, status=400, error=str(error))
    return admin_page(request, ctx, invitation_token=token)


@router.post("/admin/invitations/{invitation_id}/revoke")
def revoke_invite(
    invitation_id: int,
    request: Request,
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        service.revoke_invitation(ctx.db, actor, invitation_id)
    except ValueError as error:
        return admin_page(request, ctx, status=400, error=str(error))
    return redirect(request, ctx, "/admin")


@router.post("/admin/users")
def add_user(
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        service.create_user(
            ctx.db,
            name=fields.get("name", ""),
            login=fields.get("login", ""),
            password=fields.get("password", ""),
            role=fields.get("role", "member"),
        )
    except ValueError as error:
        return admin_page(request, ctx, status=400, error=str(error))
    return redirect(request, ctx, "/admin")


@router.post("/admin/users/{user_id}")
def edit_user(
    user_id: int,
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        service.update_user(
            ctx.db,
            actor,
            user_id=user_id,
            role=fields.get("role", ""),
            is_active=fields.get("is_active") == "1",
        )
    except ValueError as error:
        return admin_page(request, ctx, status=400, error=str(error))
    if user_id == actor.id:
        return redirect(request, ctx, "/login")
    return redirect(request, ctx, "/admin")


@router.post("/admin/users/{user_id}/sessions/revoke")
def revoke_sessions(
    user_id: int,
    request: Request,
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    service.revoke_user_sessions(ctx.db, user_id)
    return redirect(request, ctx, "/login" if user_id == actor.id else "/admin")


@router.post("/admin/users/{user_id}/password")
def reset_password(
    user_id: int,
    request: Request,
    fields: Annotated[dict, Depends(protected_form)],
    actor: Annotated[User, Depends(administrator)],
    ctx: Annotated[AuthContext, Depends(get_context)],
):
    try:
        service.reset_password(ctx.db, actor, user_id, fields.get("password", ""))
    except ValueError as error:
        return admin_page(request, ctx, status=400, error=str(error))
    return redirect(request, ctx, "/login" if user_id == actor.id else "/admin")
