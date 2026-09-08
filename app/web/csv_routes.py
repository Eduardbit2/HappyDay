"""Экспорт и одноразовый импорт после предпросмотра."""

import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import delete

from app.auth.security import token_hash, utc_now
from app.auth.web import current_user, protected_form, redirect, render
from app.birthdays.csv_io import export_csv, inspect_rows, parse_csv
from app.birthdays.service import create_birthday, create_group
from app.db.models import CsvDraft
from app.web.birthdays import Context, Fields

router = APIRouter(dependencies=[Depends(current_user), Depends(protected_form)])


@router.get("/data")
def data_page(request: Request, ctx: Context, imported: bool = False):
    return render(request, ctx, "csv.html", active="settings", imported=imported)


@router.get("/data/export")
def export_data(ctx: Context):
    return Response(
        export_csv(ctx.db),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="happyday.csv"'},
    )


def preview(request, ctx, rows, token, *, error=None):
    duplicates, new_groups, _ = inspect_rows(ctx.db, rows)
    return render(
        request,
        ctx,
        "csv_preview.html",
        active="settings",
        hide_add=True,
        rows=rows,
        draft_token=token,
        duplicates=duplicates,
        new_groups=new_groups,
        error=error,
        status=400 if error else 200,
    )


@router.post("/data/preview")
def preview_data(request: Request, ctx: Context, fields: Fields):
    try:
        rows = parse_csv(fields.get("csv", ""))
    except ValueError as error:
        return render(request, ctx, "csv.html", active="settings", error=str(error), status=400)
    ctx.db.execute(
        delete(CsvDraft).where(
            (CsvDraft.expires_at <= utc_now()) | (CsvDraft.session_hash == ctx.session.token_hash)
        )
    )
    token = secrets.token_urlsafe(32)
    ctx.db.add(
        CsvDraft(
            token_hash=token_hash(token),
            session_hash=ctx.session.token_hash,
            payload=json.dumps(rows, ensure_ascii=False),
            expires_at=utc_now() + timedelta(minutes=15),
        )
    )
    ctx.db.flush()
    return preview(request, ctx, rows, token)


@router.post("/data/import")
def import_data(request: Request, ctx: Context, fields: Fields):
    token = fields.get("draft_token", "")
    draft = ctx.db.get(CsvDraft, token_hash(token)) if len(token) == 43 else None
    if not draft or draft.session_hash != ctx.session.token_hash or draft.expires_at <= utc_now():
        raise HTTPException(400, "Предпросмотр устарел или уже использован. Загрузите CSV заново.")
    rows = json.loads(draft.payload)
    duplicates, _, group_map = inspect_rows(ctx.db, rows)
    if fields.get("confirm") != "1" or duplicates and fields.get("confirm_duplicates") != "1":
        return preview(
            request,
            ctx,
            rows,
            token,
            error="Подтвердите импорт и сохранение дублей, если они есть.",
        )
    # Повторная проверка дублей и запись проходят в одной BEGIN IMMEDIATE транзакции.
    with ctx.db.begin_nested():
        for row in rows:
            key = row["group"].casefold()
            if key not in group_map:
                group_map[key] = create_group(ctx.db, name=row["group"]).id
            person = create_birthday(
                ctx.db,
                name=row["name"],
                day=row["day"],
                month=row["month"],
                year=row["year"],
                group_id=group_map[key],
                note=row["note"],
                confirm_duplicate=True,
            )
            person.is_active = row["active"]
        ctx.db.delete(draft)
        ctx.db.flush()
    return redirect(request, ctx, "/data?imported=1")
