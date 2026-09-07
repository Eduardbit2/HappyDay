# HappyDay

Семейное web-приложение для дней рождения и персональных Telegram-напоминаний.
Требования и последовательность разработки: [PLAN.md](PLAN.md).

## Текущее состояние

Готовы каркас FastAPI, доменная модель SQLAlchemy и миграции SQLite.
При старте создается база и системная группа; перед обновлением существующей
схемы выполняется backup. Реализованы вход, профиль, закрытые приглашения
и управление семейными аккаунтами. Готов адаптивный интерфейс календаря, списка,
добавления и настроек на локальном Bootstrap. Можно добавлять дни рождения
и группы; полное редактирование, архив, CSV и Telegram — следующие этапы.
Приложение пока предназначено для локальной разработки.

Визуальная система: [дизайн и проверка](docs/design/system.md).

Первый администратор и правила входа: [Авторизация](docs/authentication.md).

Подробности, команды миграций и восстановление: [База данных](docs/database.md).

## Первый запуск на Windows (PowerShell)

Используем Python **3.12.10** и uv **0.12.10**. Версия Python закреплена
в `.python-version`, зависимости — в `uv.lock`.

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv==0.12.10
.\.venv\Scripts\uv.exe sync --locked --cache-dir .uv-cache
Copy-Item .env.example .env
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m app.auth create-admin --name "Администратор" --login admin
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

Копируйте `.env.example` только при первой настройке, чтобы сохранить свои настройки.
Активация `.venv` и изменение ExecutionPolicy не требуются.
Команда create-admin нужна один раз: она попросит пароль со скрытым вводом.
Откройте <http://127.0.0.1:8000/login>. Остановка — `Ctrl+C`.
Для автоматического перезапуска при разработке добавьте `--reload`.

Всегда используем один worker: в будущем этот же процесс запустит
Telegram polling и планировщик. Access log отключен, чтобы будущие одноразовые
коды в URL не попадали в журнал. Доверие к заголовкам прокси для локального
запуска отключено; production-настройка будет подготовлена с Compose.

## Проверки

```powershell
.\.venv\Scripts\uv.exe lock --check --cache-dir .uv-cache
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m pytest -q
```

Если после переключения между Windows sandbox и обычным пользователем pytest
сообщает об отказе в доступе к старому временному каталогу, используйте новый
каталог внутри проекта (не изменяя права системного Temp):

```powershell
New-Item -ItemType Directory -Path test-results -Force | Out-Null
$testRunPath = Join-Path (Get-Location) ("test-results/pytest-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider --basetemp $testRunPath
```

Тесты проверяют lifecycle/readiness, недопустимый Host, конфигурацию production
и UTF-8 в HTTP-ответе, `.env` и SQLite, включая `ё` и emoji.
Проверяются также миграции, backup/restore, ограничения БД и откат транзакций.
Рабочие данные не используются.

`/health/live` возвращает `200`, пока сервер отвечает.
`/health/ready` возвращает `200` после старта и `503`, если приложение не готово.
Readiness проверяет lifecycle, доступность SQLite и актуальность revision.
Endpoint пока не сообщает о готовности Telegram.

## Настройки

Параметры приложения имеют префикс `APP_`; `.env` читается в UTF-8.
Переменные процесса имеют приоритет над `.env`.

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test` или `production` |
| `APP_BASE_URL` | `http://127.0.0.1:8000` | Базовый адрес приложения |
| `APP_ALLOWED_HOSTS` | `["127.0.0.1","localhost"]` | Список хостов в формате JSON |
| `APP_DATA_DIR` | `./data` | Каталог SQLite и backups |
| `APP_LOG_LEVEL` | `INFO` | Уровень логирования приложения |
| `APP_SESSION_HOURS` | `168` | Абсолютный срок сессии в часах |

В production обязателен HTTPS, а хост `APP_BASE_URL` должен присутствовать
в `APP_ALLOWED_HOSTS`. Пустой список и wildcard запрещены. Все изменяющие
формы авторизации проверяют CSRF и Origin; cookie в production получает Secure.

Календарные расчеты будут использовать `Europe/Moscow` через `zoneinfo`;
`tzdata` закреплена для поддержки Windows. `TZ` в `.env.example` отражает
целевую зону развертывания и не меняет системные часы Windows.

## Зависимости и кодировки

Прямые зависимости описаны в `pyproject.toml`, точные версии и хеши — в `uv.lock`.
После изменения зависимостей выполните `uv lock`, `uv sync --locked` и проверки
через `.venv/Scripts/uv.exe`. Не редактируйте lock-файл вручную.
Docker на этапе развертывания должен использовать Python 3.12.10 и тот же lock.

Исходники и документация хранятся в UTF-8 с LF. Правила закреплены
в `.editorconfig` и `.gitattributes`; консольное логирование использует UTF-8.

## Структура

- `app/web` — HTTP-маршруты, затем страницы и формы.
- `app/config.py`, `app/logging.py`, `app/main.py` — настройки и запуск.
- `app/auth`, `app/db`, `app/birthdays` — доменная модель и авторизация.
- `app/bot`, `app/notifications`, `app/scheduler` — Telegram и напоминания.
- `templates`, `static` — будущий интерфейс Jinja/Bootstrap.
- `tests` — автоматические проверки.

## Git workflow

Локальный репозиторий инициализирован с веткой `main`. Следующие изменения ведем
в feature-ветках и переносим в `main` после проверок. Репозиторий GitHub:
[Eduardbit2/HappyDay](https://github.com/Eduardbit2/HappyDay).
Remote `origin` использует HTTPS; CI и Docker будут добавлены по плану.

`.env`, `.venv`, SQLite/WAL, backups, CSV, логи и ключи исключены из Git
и Docker context. Не храните секреты в исходниках или документации.
