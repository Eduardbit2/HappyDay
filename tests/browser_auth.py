"""Браузерная проверка на отдельной базе в test-results; сервер запускается извне."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from app.auth.service import bootstrap_admin
from app.db.engine import create_db_engine

ROOT = Path(__file__).resolve().parents[1]
data_dir = Path(os.environ["APP_DATA_DIR"]).resolve()
if not data_dir.is_relative_to(ROOT / "test-results"):
    raise RuntimeError("Для browser-теста нужна отдельная APP_DATA_DIR внутри test-results")
base_url = os.environ["HAPPYDAY_BROWSER_URL"]
password = "Только-тестовый-пароль-123"
engine = create_db_engine(data_dir / "happyday.db")
try:
    with Session(engine.execution_options(sqlite_write=True)) as db, db.begin():
        bootstrap_admin(db, name="Алёна Ёжик", login="admin", password=password)
finally:
    engine.dispose()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True, channel=os.environ.get("HAPPYDAY_BROWSER_CHANNEL", "msedge") or None
    )
    try:
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url + "/login")
        page.wait_for_load_state("networkidle")
        print("Rendered labels:", page.locator("label").all_text_contents())
        page.screenshot(path=str(data_dir / "login-desktop.png"), full_page=True)
        page.get_by_label("Логин", exact=True).fill("admin")
        page.get_by_label("Пароль", exact=True).fill(password)
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url.endswith("/login")
        ) as login_response:
            page.get_by_role("button", name="Войти", exact=True).click()
        print("Login POST status:", login_response.value.status, flush=True)
        if login_response.value.status != 303:
            print("Login result:", page.locator("body").inner_text(), flush=True)
            page.screenshot(path=str(data_dir / "login-failure.png"), full_page=True)
            raise RuntimeError("Browser login failed")
        page.wait_for_url(base_url + "/")
        assert page.get_by_role("heading", name="Ближайшие", exact=True).is_visible()
        page.goto(base_url + "/settings")
        page.get_by_role("link", name="Семья Аккаунты и приглашения", exact=True).click()
        page.wait_for_load_state("networkidle")
        print("Admin actions:", page.get_by_role("button").all_text_contents())
        page.get_by_role("button", name="Создать приглашение").click()
        page.get_by_label("Код приглашения").wait_for()
        code = page.get_by_label("Код приглашения").input_value()
        member_context = browser.new_context(viewport={"width": 390, "height": 844})
        member = member_context.new_page()
        member.goto(base_url + "/join")
        member.wait_for_load_state("networkidle")
        print("Join labels:", member.locator("label").all_text_contents())
        member.get_by_label("Код приглашения", exact=True).fill(code)
        member.get_by_label("Ваше имя", exact=True).fill("Лёля 🎂")
        member.get_by_label("Логин", exact=True).fill("лёля")
        member.get_by_label("Пароль", exact=True).fill(password)
        member.get_by_label("Повторите пароль", exact=True).fill(password)
        member.get_by_role("button", name="Создать аккаунт").click()
        member.wait_for_url(base_url + "/")
        assert member.get_by_role("heading", name="Ближайшие", exact=True).is_visible()
        page.goto(base_url + "/admin")
        page.wait_for_load_state("networkidle")
        card = page.locator("article.member").filter(
            has=page.get_by_role("heading", name="Лёля 🎂", exact=True)
        )
        card.get_by_label("Аккаунт активен").uncheck()
        card.get_by_role("button", name="Сохранить доступ").click()
        page.wait_for_url(base_url + "/admin")
        member.goto(base_url + "/")
        member.wait_for_url(base_url + "/login")
        for width in (320, 390, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            member.set_viewport_size({"width": width, "height": 900})
            assert member.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        member.set_viewport_size({"width": 320, "height": 800})
        member.screenshot(path=str(data_dir / "login-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 900})
        page.screenshot(path=str(data_dir / "admin-mobile.png"), full_page=True)
        page.goto(base_url + "/")
        page.get_by_role("link", name="Добавить", exact=True).click()
        page.wait_for_load_state("networkidle")
        print("Birthday labels:", page.locator("label").all_text_contents())
        page.get_by_label("Имя и фамилия", exact=True).fill("Анна Проверка 🎂")
        page.get_by_label("День", exact=True).fill("12")
        page.get_by_label("Месяц", exact=True).select_option("9")
        page.get_by_role("button", name="Сохранить день рождения").click()
        page.get_by_role("status").filter(has_text="День рождения сохранен").wait_for()
        assert page.get_by_role("heading", name="Анна Проверка 🎂").is_visible()
        detail_url = page.url.split("?")[0]
        page.get_by_role("link", name="Редактировать", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.get_by_label("Имя и фамилия", exact=True).fill("Анна Изменённая 🎂")
        page.get_by_role("button", name="Сохранить день рождения").click()
        page.get_by_role("heading", name="Анна Изменённая 🎂").wait_for()
        page.get_by_role("button", name="В архив", exact=True).click()
        page.wait_for_url(base_url + "/birthdays/archive")
        page.get_by_role("link").filter(has_text="Анна Изменённая 🎂").click()
        page.get_by_role("button", name="Восстановить", exact=True).click()
        page.get_by_role("status").filter(has_text="День рождения сохранен").wait_for()
        page.goto(base_url + "/groups")
        page.get_by_label("Название", exact=True).fill("Браузерная группа")
        page.get_by_role("button", name="Создать группу").click()
        page.get_by_role("link").filter(has_text="Браузерная группа").click()
        group_edit_url = page.url
        page.get_by_label("Иконка или emoji").fill("🎂")
        page.get_by_label("Порядок сортировки").fill("3")
        page.get_by_role("button", name="Сохранить группу").click()
        page.wait_for_url(base_url + "/groups")
        page.goto(base_url + "/data")
        page.wait_for_load_state("networkidle")
        print("CSV labels:", page.locator("label").all_text_contents())
        csv_text = (
            "name;day;month;year;group;note;active\n"
            "Лёля CSV 🎂;29;2;;Браузерная группа;Любит чай;0\n"
        )
        page.get_by_label("Файл CSV", exact=True).set_input_files(
            {
                "name": "дни-рождения.csv",
                "mimeType": "text/csv",
                "buffer": csv_text.encode("utf-8-sig"),
            }
        )
        page.wait_for_function("document.getElementById('csv').value.includes('Лёля CSV')")
        page.get_by_role("button", name="Предпросмотр", exact=True).click()
        page.get_by_role("heading", name="Предпросмотр CSV", exact=True).wait_for()
        page.screenshot(path=str(data_dir / "csv-preview-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_label("Подтверждаю добавление записей и новых групп").check()
        page.get_by_role("button", name="Импортировать", exact=True).click()
        page.get_by_role("status").filter(has_text="Импорт завершен").wait_for()
        with page.expect_download() as download_info:
            page.get_by_role("link", name="Скачать CSV", exact=True).click()
        download = download_info.value
        export_path = data_dir / "download.csv"
        download.save_as(export_path)
        assert "Лёля CSV 🎂" in export_path.read_text(encoding="utf-8-sig")
        page.goto(group_edit_url)
        page.get_by_label("Подтверждаю перенос записей и удаление группы").check()
        page.get_by_role("button", name="Удалить группу", exact=True).click()
        page.wait_for_url(base_url + "/groups")
        page.goto(base_url + "/birthdays/archive")
        page.get_by_role("link").filter(has_text="Лёля CSV 🎂").click()
        assert "Без группы" in page.locator("main").inner_text()
        page.get_by_role("link", name="Удалить окончательно", exact=True).click()
        page.get_by_label("Подтверждаю окончательное удаление").check()
        page.get_by_role("button", name="Удалить окончательно", exact=True).click()
        page.wait_for_url(base_url + "/birthdays/archive")
        assert "Лёля CSV 🎂" not in page.locator("main").inner_text()
        for route in (
            "/",
            "/birthdays",
            "/birthdays/new",
            "/settings",
            "/groups",
            "/groups/1/edit",
            "/data",
            "/birthdays/archive",
            detail_url.removeprefix(base_url) + "/edit",
            "/profile",
            "/admin",
            "/admin/deliveries",
        ):
            page.goto(base_url + route)
            page.wait_for_load_state("networkidle")
            for width in (320, 360, 390, 430, 768, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    route,
                    width,
                )
        page.emulate_media(reduced_motion="reduce")
        assert page.locator(".workspace").evaluate("e=>getComputedStyle(e).animationName") == "none"
        assert not errors, errors
        print("Browser auth flow, revocation and responsive widths: OK")
    finally:
        browser.close()
