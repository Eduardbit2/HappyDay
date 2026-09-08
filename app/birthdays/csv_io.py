"""CSV UTF-8: проверка всего файла до записи, обратимая защита формул при экспорте."""

import csv
import io

from sqlalchemy import select

from app.birthdays.service import normalize_text, validate_birthday
from app.db.models import Birthday, Group

HEADERS = ("name", "day", "month", "year", "group", "note", "active")
MAX_BYTES = 262_144
MAX_ROWS = 500
DANGEROUS = "=+-@\t\r\n"


def needs_escape(value: str) -> bool:
    return bool(value) and (
        value[0] in DANGEROUS + "'" or bool(value.lstrip()) and value.lstrip()[0] in DANGEROUS
    )


def escape_cell(value: str) -> str:
    return "'" + value if needs_escape(value) else value


def unescape_cell(value: str) -> str:
    return value[1:] if value.startswith("'") and needs_escape(value[1:]) else value


def parse_csv(text: str) -> list[dict]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("CSV должен быть не больше 256 КБ")
    try:
        reader = csv.DictReader(
            io.StringIO(text.lstrip("\ufeff"), newline=""), delimiter=";", strict=True
        )
        if reader.fieldnames != list(HEADERS):
            raise ValueError(
                "Нужны столбцы name;day;month;year;group;note;active в указанном порядке"
            )
        rows = []
        for line, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("неверное число столбцов")
                if len(rows) >= MAX_ROWS:
                    raise ValueError("не более 500 записей в одном файле")
                name = unescape_cell(row["name"])
                day, month = int(row["day"]), int(row["month"])
                year = int(row["year"]) if row["year"].strip() else None
                name = validate_birthday(name, day, month, year)
                group = normalize_text(unescape_cell(row["group"])) or "Без группы"
                note = unescape_cell(row["note"])
                if len(group) > 100 or len(note) > 2000 or "\x00" in name + group + note:
                    raise ValueError("слишком длинное поле или недопустимый символ")
                if row["active"] not in ("0", "1"):
                    raise ValueError("active должен быть 1 или 0")
                rows.append(
                    dict(
                        name=name,
                        day=day,
                        month=month,
                        year=year,
                        group=group,
                        note=note,
                        active=row["active"] == "1",
                    )
                )
            except ValueError as error:
                raise ValueError(f"Строка {line}: {error}") from None
    except csv.Error:
        raise ValueError("Не удалось разобрать CSV: проверьте кавычки и разделители") from None
    if not rows:
        raise ValueError("CSV не содержит записей")
    return rows


def export_csv(session) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_ALL)
    writer.writerow(HEADERS)
    for person, group in session.execute(select(Birthday, Group).join(Group).order_by(Birthday.id)):
        writer.writerow(
            (
                escape_cell(person.name),
                person.day,
                person.month,
                person.year or "",
                escape_cell(group.name),
                escape_cell(person.note),
                int(person.is_active),
            )
        )
    return output.getvalue().encode("utf-8-sig")


def duplicate_key(name, day, month, year):
    return normalize_text(name).casefold(), day, month, year


def inspect_rows(session, rows):
    known = {
        duplicate_key(p.name, p.day, p.month, p.year) for p in session.scalars(select(Birthday))
    }
    duplicates = []
    for index, row in enumerate(rows, start=1):
        key = duplicate_key(row["name"], row["day"], row["month"], row["year"])
        if key in known:
            duplicates.append(index)
        known.add(key)
    group_map = {}
    for group in session.scalars(select(Group).order_by(Group.id)):
        group_map.setdefault(normalize_text(group.name).casefold(), group.id)
    new_groups = sorted(
        {row["group"] for row in rows if row["group"].casefold() not in group_map}, key=str.casefold
    )
    return duplicates, new_groups, group_map
