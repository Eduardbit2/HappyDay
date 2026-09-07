"""Операции авторизации. Вызывающий код использует транзакцию BEGIN IMMEDIATE."""

from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth.security import (
    hash_password,
    hasher,
    new_token,
    normalize_login,
    token_hash,
    utc_now,
    validate_identity,
    verify_password,
)
from app.db.models import AuthSession, Invitation, LoginThrottle, User


class LoginLimited(ValueError):
    pass


def create_user(db: Session, *, name: str, login: str, password: str, role: str = "member") -> User:
    name, login = validate_identity(name, login)
    if role not in ("admin", "member"):
        raise ValueError("Неизвестная роль")
    if db.scalar(select(User.id).where(User.login == login)) is not None:
        raise ValueError("Этот логин уже занят")
    user = User(name=name, login=login, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def bootstrap_admin(db: Session, *, name: str, login: str, password: str) -> User:
    if db.scalar(select(User.id).where(User.role == "admin")) is not None:
        raise ValueError("Администратор уже существует; используйте управление аккаунтами")
    return create_user(db, name=name, login=login, password=password, role="admin")


def create_session(
    db: Session, *, user_id: int | None = None, lifetime_hours: int = 168
) -> tuple[AuthSession, str]:
    now = utc_now()
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    token = new_token()
    session = AuthSession(
        token_hash=token_hash(token),
        csrf_token=new_token(),
        user_id=user_id,
        expires_at=now + (timedelta(hours=lifetime_hours) if user_id else timedelta(minutes=30)),
    )
    db.add(session)
    db.flush()
    return session, token


def rotate_session(
    db: Session, old: AuthSession, user: User, lifetime_hours: int
) -> tuple[AuthSession, str]:
    db.delete(old)
    db.flush()
    return create_session(db, user_id=user.id, lifetime_hours=lifetime_hours)


def throttle_login(db: Session, login: str, address: str) -> None:
    now = utc_now()
    db.execute(delete(LoginThrottle).where(LoginThrottle.expires_at <= now))
    keys = [(token_hash("login:" + normalize_login(login)), 5), (token_hash("ip:" + address), 30)]
    for key, limit in keys:
        bucket = db.get(LoginThrottle, key)
        if bucket is not None and bucket.attempts >= limit:
            raise LoginLimited("Слишком много попыток входа. Повторите через 15 минут")
    for key, _limit in keys:
        bucket = db.get(LoginThrottle, key)
        if bucket is None:
            bucket = LoginThrottle(key=key, attempts=0, expires_at=now + timedelta(minutes=15))
            db.add(bucket)
        bucket.attempts += 1
    db.flush()


def authenticate(db: Session, login: str, password: str, address: str) -> User | None:
    throttle_login(db, login, address)
    user = db.scalar(select(User).where(User.login == normalize_login(login)))
    valid = verify_password(user.password_hash if user else None, password)
    if not user or not valid or not user.is_active:
        return None
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(password)
    db.execute(delete(LoginThrottle).where(LoginThrottle.key == token_hash("login:" + user.login)))
    return user


def require_admin(user: User) -> None:
    if not user.is_active or user.role != "admin":
        raise PermissionError("Доступ только для администратора")


def create_invitation(db: Session, actor: User, *, role: str = "member") -> tuple[Invitation, str]:
    require_admin(actor)
    if role not in ("admin", "member"):
        raise ValueError("Неизвестная роль")
    token = new_token()
    invite = Invitation(
        token_hash=token_hash(token),
        created_by=actor.id,
        role=role,
        expires_at=utc_now() + timedelta(hours=48),
    )
    db.add(invite)
    db.flush()
    return invite, token


def accept_invitation(db: Session, *, token: str, name: str, login: str, password: str) -> User:
    invite = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    if not invite or invite.revoked_at or invite.accepted_at or invite.expires_at <= utc_now():
        raise ValueError("Приглашение недействительно или срок его действия истек")
    issuer = db.get(User, invite.created_by)
    if not issuer or not issuer.is_active or issuer.role != "admin":
        raise ValueError("Приглашение недействительно или срок его действия истек")
    user = create_user(db, name=name, login=login, password=password, role=invite.role)
    invite.accepted_at = utc_now()
    return user


def revoke_invitation(db: Session, actor: User, invitation_id: int) -> None:
    require_admin(actor)
    invite = db.get(Invitation, invitation_id)
    if not invite:
        raise ValueError("Приглашение не найдено")
    invite.revoked_at = utc_now()


def revoke_user_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


def update_user(db: Session, actor: User, *, user_id: int, role: str, is_active: bool) -> None:
    require_admin(actor)
    user = db.get(User, user_id)
    if user is None or role not in ("admin", "member"):
        raise ValueError("Пользователь или роль не найдены")
    if user.role == "admin" and user.is_active and (role != "admin" or not is_active):
        count = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        if count <= 1:
            raise ValueError("Нельзя отключить или понизить последнего администратора")
    if user.role != role or user.is_active != is_active:
        revoke_user_sessions(db, user.id)
    user.role, user.is_active = role, is_active
    if role != "admin" or not is_active:
        db.execute(
            update(Invitation)
            .where(
                Invitation.created_by == user.id,
                Invitation.accepted_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
    db.flush()


def change_password(db: Session, user: User, current_password: str, password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise ValueError("Текущий пароль неверен")
    user.password_hash = hash_password(password)
    revoke_user_sessions(db, user.id)


def reset_password(db: Session, actor: User, user_id: int, password: str) -> None:
    require_admin(actor)
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    user.password_hash = hash_password(password)
    revoke_user_sessions(db, user.id)
