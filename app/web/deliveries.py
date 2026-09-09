"""Журнал доступен только администратору."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.web import administrator, render
from app.db.models import Birthday, Delivery, DeliveryAttempt, EveningAttempt, EveningReminder, User
from app.web.birthdays import Context

router = APIRouter(dependencies=[Depends(administrator)])
STATUS_LABELS = {
    "pending": "В очереди",
    "sending": "Отправляется",
    "sent": "Доставлено",
    "retry": "Ожидает повтора",
    "failed": "Ошибка",
    "cancelled": "Отменено",
}


@router.get("/admin/deliveries")
def deliveries(request: Request, ctx: Context, status: str = "", kind: str = ""):
    if status and status not in STATUS_LABELS:
        raise HTTPException(400, "Неизвестный статус")
    if kind not in ("", "evening"):
        raise HTTPException(400, "Неизвестный вид напоминания")
    model = EveningReminder if kind == "evening" else Delivery
    attempt_model = EveningAttempt if kind == "evening" else DeliveryAttempt
    query = (
        select(model, Birthday.name, User.name)
        .join(Birthday, model.birthday_id == Birthday.id)
        .join(User, model.user_id == User.id)
        .order_by(model.id.desc())
        .limit(100)
    )
    if status:
        query = query.where(model.status == status)
    rows = list(ctx.db.execute(query))
    ids = [row[0].id for row in rows]
    attempts = {}
    if ids:
        for attempt in ctx.db.scalars(
            select(attempt_model)
            .where(attempt_model.delivery_id.in_(ids))
            .order_by(attempt_model.attempt_number)
        ):
            attempts.setdefault(attempt.delivery_id, []).append(attempt)
    return render(
        request,
        ctx,
        "deliveries.html",
        active="settings",
        rows=rows,
        attempts=attempts,
        labels=STATUS_LABELS,
        selected_status=status,
        selected_kind=kind,
        transport_ready=request.app.state.notification_engine.sender is not None,
    )
