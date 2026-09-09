# Автоматические проверки

Workflow [CI](../.github/workflows/ci.yml) запускается на push, pull request
и вручную через GitHub Actions. Доступ к репозиторию — только чтение.
Deploy и публикация образов не выполняются; production-секреты не требуются.

## Состав pipeline

- Ubuntu и Windows: Python из `.python-version`, uv 0.12.10, установка
  `uv sync --locked`, Ruff, весь pytest и отдельная проверка миграций.
- Ubuntu: изолированные браузерные сценарии Chromium на ширинах 320–1280 px.
- После успешных проверок: сборка Docker и smoke-тест образа — readiness,
  Docker healthcheck, запуск без root, русская страница входа, запись SQLite
  и сохранение данных после перезапуска контейнера.
- JUnit XML и браузерные PNG сохраняются на 7 дней; SQLite и файлы окружения
  в артефакты не включаются.

Повторный запуск для той же ветки отменяет предыдущий. Версии GitHub Actions
закреплены по commit SHA. Workflow не использует `pull_request_target`.
Telegram в тестах отключен, HTTP-контракты проверяются через mock transport.

## Локальная проверка

Из корня проекта, после установки зависимостей:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_migrations
docker build --tag happyday:ci .
.\.venv\Scripts\python.exe scripts/check_docker.py --image happyday:ci
```

Docker smoke создает временный контейнер без подключения рабочей базы, проверяет
его и удаляет в `finally`. Требуется запущенный Docker Engine с Linux containers.

Dockerfile использует Python 3.12.10 и production-зависимости из `uv.lock`.
Пользователь контейнера — UID/GID 10001, каталог базы — `/data`.
Для развертывания потребуется отдельный постоянный volume и настройка HTTPS
reverse proxy; Docker smoke проверяет перезапуск, а не удаление контейнера с данными.
Compose и приемка на Synology относятся к следующему этапу.

Основные источники: [GitHub Actions для Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python),
[uv в Docker](https://docs.astral.sh/uv/guides/integration/docker/).
