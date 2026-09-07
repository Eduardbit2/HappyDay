"""Рабочая оболочка календаря и базовое добавление через общие доменные правила."""

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.web import AuthContext, current_user, get_context, protected_form, redirect, render
from app.birthdays.service import (
    DuplicateBirthdayError,
    age_in_year,
    create_birthday,
    create_group,
    normalize_text,
    occurrence_in_year,
)
from app.db.models import Birthday, Group

router = APIRouter(dependencies=[Depends(current_user), Depends(protected_form)])
Context = Annotated[AuthContext, Depends(get_context)]
Fields = Annotated[dict, Depends(protected_form)]
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
SHORT_MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def plural(number: int, forms: tuple[str, str, str]) -> str:
    return (
        forms[2]
        if 11 <= number % 100 <= 14
        else forms[0]
        if number % 10 == 1
        else forms[1]
        if 2 <= number % 10 <= 4
        else forms[2]
    )


def event_rows(ctx: AuthContext, today: date | None = None):
    today = today or datetime.now(ZoneInfo("Europe/Moscow")).date()
    rows = []
    for person, group in ctx.db.execute(
        select(Birthday, Group).join(Group).where(Birthday.is_active.is_(True))
    ):
        occurrence = occurrence_in_year(person, today.year)
        if occurrence < today:
            occurrence = occurrence_in_year(person, today.year + 1)
        days = (occurrence - today).days
        age = age_in_year(person, occurrence.year)
        rows.append(
            {
                "person": person,
                "group": group,
                "date": occurrence,
                "day": f"{occurrence.day:02}",
                "month": MONTHS[occurrence.month - 1],
                "short_month": SHORT_MONTHS[occurrence.month - 1],
                "when": "Сегодня"
                if days == 0
                else "Завтра"
                if days == 1
                else f"Через {days} {plural(days, ('день', 'дня', 'дней'))}",
                "age": f"{age} {plural(age, ('год', 'года', 'лет'))}" if age is not None else None,
            }
        )
    return sorted(
        rows, key=lambda item: (item["date"], item["person"].name.casefold(), item["person"].id)
    )


def groups(ctx):
    return list(ctx.db.scalars(select(Group).order_by(Group.sort_order, Group.id)))


@router.get("/")
def home(request: Request, ctx: Context):
    events = event_rows(ctx)
    return render(request, ctx, "home.html", events=events[:8], active="upcoming")


@router.get("/birthdays")
def birthday_list(request: Request, ctx: Context, q: str = "", group: str = "", month: str = ""):
    group = int(group) if len(group) <= 10 and group.isdecimal() else None
    month = int(month) if len(month) <= 2 and month.isdecimal() else None
    events = event_rows(ctx)
    q = q[:200]
    query = normalize_text(q).casefold()
    filtered = [
        item
        for item in events
        if (not query or query in item["person"].name.casefold())
        and (not group or item["group"].id == group)
        and (not month or item["person"].month == month)
    ]
    return render(
        request,
        ctx,
        "birthdays.html",
        events=filtered,
        groups=groups(ctx),
        months=MONTHS,
        q=q,
        selected_group=group,
        selected_month=month,
        active="all",
    )


def birthday_form(request, ctx, *, values=None, errors=None, duplicate=False):
    return render(
        request,
        ctx,
        "birthday_form.html",
        status=400 if errors or duplicate else 200,
        values=values or {},
        errors=errors or {},
        duplicate=duplicate,
        groups=groups(ctx),
        months=MONTHS,
        active="all",
        hide_add=True,
    )


@router.get("/birthdays/new")
def add_page(request: Request, ctx: Context):
    return birthday_form(request, ctx)


@router.post("/birthdays/new")
def add_birthday(request: Request, ctx: Context, fields: Fields):
    errors = {}
    values = {
        key: fields.get(key, "") for key in ("name", "day", "month", "year", "group_id", "note")
    }
    if not normalize_text(values["name"]):
        errors["name"] = "Укажите имя"
    numbers = {}
    for key, label in (
        ("day", "Укажите день от 1 до 31"),
        ("month", "Выберите месяц"),
        ("year", "Укажите год числом"),
        ("group_id", "Выберите группу"),
    ):
        try:
            numbers[key] = int(values[key]) if values[key] else None
        except ValueError:
            errors[key] = label
    if numbers.get("day") is None or not 1 <= numbers.get("day", 0) <= 31:
        errors["day"] = "Укажите день от 1 до 31"
    if numbers.get("month") is None or not 1 <= numbers.get("month", 0) <= 12:
        errors["month"] = "Выберите месяц"
    if (
        numbers.get("year") is not None
        and not 1 <= numbers["year"] <= datetime.now(ZoneInfo("Europe/Moscow")).year
    ):
        errors["year"] = "Год рождения не может быть будущим"
    if len(values["note"]) > 2000:
        errors["note"] = "Не более 2000 символов"
    if errors:
        return birthday_form(request, ctx, values=values, errors=errors)
    try:
        person = create_birthday(
            ctx.db,
            name=values["name"],
            day=numbers["day"],
            month=numbers["month"],
            year=numbers["year"],
            group_id=numbers["group_id"] or 1,
            note=values["note"],
            confirm_duplicate=fields.get("confirm_duplicate") == "1",
        )
    except DuplicateBirthdayError:
        return birthday_form(request, ctx, values=values, duplicate=True)
    except ValueError:
        return birthday_form(
            request, ctx, values=values, errors={"day": "Проверьте дату, имя и выбранную группу"}
        )
    return redirect(request, ctx, f"/birthdays/{person.id}?saved=1")


@router.get("/birthdays/{birthday_id}")
def birthday_detail(birthday_id: int, request: Request, ctx: Context, saved: bool = False):
    event = next((item for item in event_rows(ctx) if item["person"].id == birthday_id), None)
    if event is None:
        raise HTTPException(404, "Запись не найдена")
    return render(request, ctx, "birthday_detail.html", event=event, saved=saved, active="all")


@router.get("/settings")
def settings(request: Request, ctx: Context):
    return render(request, ctx, "settings.html", active="settings")


@router.get("/groups")
def group_list(request: Request, ctx: Context):
    return render(request, ctx, "groups.html", groups=groups(ctx), active="settings")


@router.post("/groups")
def add_group(request: Request, ctx: Context, fields: Fields):
    try:
        create_group(ctx.db, name=fields.get("name", ""), color=fields.get("color", "#8A8178"))
    except ValueError as error:
        return render(
            request,
            ctx,
            "groups.html",
            groups=groups(ctx),
            active="settings",
            error=str(error),
            status=400,
        )
    return redirect(request, ctx, "/groups")
