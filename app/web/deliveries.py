"""Журнал доступен только администратору."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.web import administrator, render
from app.db.models import Birthday, Delivery, DeliveryAttempt, User
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
def deliveries(request: Request, ctx: Context, status: str = ""):
    if status and status not in STATUS_LABELS:
        raise HTTPException(400, "Неизвестный статус")
    query = (
        select(Delivery, Birthday.name, User.name)
        .join(Birthday, Delivery.birthday_id == Birthday.id)
        .join(User, Delivery.user_id == User.id)
        .order_by(Delivery.id.desc())
        .limit(100)
    )
    if status:
        query = query.where(Delivery.status == status)
    rows = list(ctx.db.execute(query))
    ids = [row[0].id for row in rows]
    attempts = {}
    if ids:
        for attempt in ctx.db.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.delivery_id.in_(ids))
            .order_by(DeliveryAttempt.attempt_number)
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
        transport_ready=request.app.state.notification_engine.sender is not None,
    )
