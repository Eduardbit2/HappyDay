# Развертывание на Synology / Dockhand

Подготовлены `compose.yaml` и `deploy/synology.env.example`.
Развертывание на реальном NAS пока не выполнялось: нужны домен, модель NAS,
версия DSM и доступный Dockhand. Команды ниже выполняются на Synology
из каталога выбранной версии репозитория, если не указано иное.

## Параметры и каталог данных

В настройках окружения Git Stack задайте значения из шаблона:

| Параметр | Значение |
| --- | --- |
| HAPPYDAY_IMAGE_TAG | Короткий SHA выбранного коммита, без latest |
| HAPPYDAY_DATA_PATH | Существующий абсолютный путь, например /volume1/docker/happyday/data |
| APP_BASE_URL | Полный публичный HTTPS URL без дополнительного пути |
| APP_ALLOWED_HOSTS | JSON-массив доменов, например ["happyday.example.com"] |
| HAPPYDAY_TRUSTED_PROXIES | Точный IP источника соединений DSM proxy, видимый контейнеру |
| APP_TELEGRAM_TOKEN | Токен BotFather; пустое значение отключает Telegram |

`127.0.0.1` в шаблоне trusted proxies — начальное значение, а не предположение
о сети NAS. При стандартном Docker bridge соединение DSM может приходить с адреса
шлюза этой сети. Посмотрите сеть контейнера через Inspect и ее Gateway, подтвердите
фактический источник и задайте конкретный адрес. Не используйте `*`.
Прокси должен перезаписывать X-Forwarded-For и X-Forwarded-Proto данными соединения.
Это нужно в том числе для корректного ограничения попыток входа по IP.

Пользователь приложения имеет UID/GID 10001. Создайте отдельный каталог и
дайте ему право записи. Для нового пустого каталога:

```sh
sudo mkdir -p /volume1/docker/happyday/data
sudo chown 10001:10001 /volume1/docker/happyday/data
sudo chmod 750 /volume1/docker/happyday/data
```

Если DSM ACL ограничивает доступ, разрешите проход к каталогу и запись UID 10001.
Compose не создает отсутствующий bind-каталог автоматически. Не меняйте владельца
всего /volume1/docker. SQLite, WAL, SHM и backups должны оставаться внутри /data.

Рабочие параметры храните в Dockhand. Для CLI можно создать отдельный файл
`deploy/synology.env` с правами 600 (он исключен из Git). Команды с
`docker compose config --quiet` проверяют конфигурацию без печати токена.

## Git Stack и первый запуск

1. В GitHub добавьте отдельный deploy key только для чтения этого репозитория.
   Приватный ключ сохраните в Git credentials Dockhand.
2. Создайте Git Stack для `git@github.com:Eduardbit2/HappyDay.git`, ветка `main`,
   Compose path `compose.yaml`. Задайте параметры выше.
3. Отключите автоматический deploy по расписанию/webhook; обновляйте вручную после
   успешного CI. Выполните сборку из выбранного коммита и запомните SHA вместе с тегом.
   Если Dockhand пропускает сборку при неизменном Compose, используйте явный rebuild.
4. Запустите один экземпляр app. Миграции выполнятся перед готовностью сервера.
   Проверьте состояние healthy и отсутствие ошибок в логах.

Для проверки через CLI:

```sh
docker compose --env-file deploy/synology.env config --quiet
docker compose --env-file deploy/synology.env build app
docker compose --env-file deploy/synology.env up -d --no-build app
docker compose --env-file deploy/synology.env ps
docker compose --env-file deploy/synology.env exec app python -m app.auth create-admin --name "Администратор" --login admin
```

Последняя команда нужна только для новой базы: пароль запрашивается скрыто.
При переносе существующей базы используйте текущую учетную запись.

### Перенос с Windows

Остановите локальное приложение и polling перед запуском того же Telegram-бота
на NAS. На Windows создайте согласованный снимок командой
`.\.venv\Scripts\python.exe -m app.db backup`.
Перенесите полученный файл по доверенному каналу в новый пустой каталог NAS
под именем `happyday.db`, назначьте владельца 10001:10001 и права 640.
Не переносите отдельно WAL/SHM и не заменяйте базу работающего приложения.
Так сохраняются аккаунты, привязки и история; CSV переносит только данные календаря.

## DSM Reverse Proxy

В DSM 7: Панель управления → Портал входа → Дополнительно → Обратный прокси.
Создайте правило HTTPS для выбранного домена на 443 → HTTP 127.0.0.1:8787.
Привяжите действующий сертификат к домену. Сохраните публичный Host
и передавайте достоверные X-Forwarded-For / X-Forwarded-Proto.
Настройку DNS и сертификата выполняет владелец NAS.

Снаружи используйте только HTTPS. Порт 8787 опубликован на loopback NAS;
его не нужно открывать на роутере. В production /health/live и /health/ready
доступны только с loopback внутри контейнера. Docker HEALTHCHECK сам использует
этот путь и Host из APP_BASE_URL. Проверка публичного URL /health/ready вернет 404.

После настройки проверьте вход, сохранение записи, выход, открытие web-ссылки
из Telegram и одну тестовую отправку через профиль. Убедитесь, что запущен
единственный polling-процесс для токена.

## Backup и восстановление

Создать согласованный снимок работающей базы:

```sh
docker compose --env-file deploy/synology.env exec -T app python -m app.db backup
```

Команда использует SQLite Online Backup API, включая подтвержденные данные WAL,
проверяет целостность и печатает путь готового файла в /data/backups.
Ее можно добавить в DSM Task Scheduler после настройки пути checkout и env-файла.
Hyper Backup должен копировать готовые *.db снимки, а не один активный happyday.db.
Сохраняйте копию вне самого NAS. Очистка старых снимков пока ручная; сначала
проверьте внешнюю копию и восстановление. Автоматическое удаление не настроено.

Для восстановления остановите app. Создайте новый пустой каталог данных,
скопируйте выбранный backup как happyday.db и задайте права UID/GID 10001.
Переключите HAPPYDAY_DATA_PATH на этот каталог; старый каталог оставьте для возврата.
Не смешивайте восстановленный файл с WAL/SHM предыдущей базы.

Выберите соответствующую версию кода/образа, затем проверьте снимок без запуска polling:

```sh
docker compose --env-file deploy/synology.env run --rm --no-deps app python -m app.db check
docker compose --env-file deploy/synology.env up -d --no-build app
```

Если схема backup старше выбранного кода, `check` сообщит об этом. Либо выберите
совпадающий образ, либо явно выполните `app.db upgrade` на восстановленном каталоге
при остановленном app, затем повторите check. Обычный запуск тоже выполняет upgrade,
поэтому версию кода следует выбрать заранее.

## Обновление и откат

Перед обновлением создайте backup, сохраните текущий SHA, тег image и env-параметры.
Выберите новый коммит с успешным CI, обновите тег, явно соберите образ и выполните
ручной deploy. Проверьте healthy, вход и действия формы. Новая миграция автоматически
создает дополнительный backup перед изменением схемы.

При ошибке остановите app. Для отката используйте предыдущий код/образ и
предмиграционный backup в новом каталоге. Простая смена image не откатывает SQLite.
Не используйте downgrade на рабочей базе. Возврат к backup теряет изменения,
сделанные после его создания.

## Что еще проверить на реальном NAS

- Модель/архитектуру NAS, версию Docker Compose и возможность локальной сборки.
- UID/ACL bind mount, trusted proxy IP, сертификат и secure-cookie через HTTPS.
- Реальный Android/Chrome: клавиатуру, переключение полей, поворот экрана.
- Ручное обновление, внешний backup и восстановление выбранной версии.
- Доставку Telegram без второго polling-процесса.

Источники: [Docker Compose](https://docs.docker.com/reference/compose-file/services/),
[руководство Dockhand](https://dockhand.pro/manual/),
[DSM Reverse Proxy](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_login_portal_advanced?version=7).
