> Сверка по предоставленному XML, отдельные цены/предзаказ и оставшиеся SKU: [seller-xml.md](seller-xml.md).

> Актуальная сверка реального кабинета и исправления: [kaspi-live-sync.md](kaspi-live-sync.md).
> Рабочий отчёт показывает полученные данные API; архивные расчёты доступны только в явном demo-режиме.

# Запуск: архивное приложение и исходная app.db

Основа — kaspibot.zip. Один сервер, один файл app.db, исходные модели/страницы
и все старые данные. Модуль /management работает с теми же products/stock_qty.
Двух баз и переноса каталогов между ними больше нет.

## Комплект с данными

Распаковать kaspi-stock-archive-first-with-data.zip в отдельную папку.
Внутри app.db с исходными данными, неизменённый backups/archive-original.db
и исходники. .env и API-ключи не включены. В GitHub база не публикуется.
Если скачан только код из GitHub, скопировать свою архивную app.db рядом с app.py
**перед** init-db; иначе будет создана пустая база для самостоятельного заполнения.

Python3.12, PowerShell из папки с app.py:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
$env:KSM_SECRET_KEY = (.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user --role admin admin
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

Пароль задаётся вами. Вход: http://127.0.0.1:5000/management/login.
Для сотрудника: create-user --role employee employee. Сохранить KSM_SECRET_KEY
у себя и задавать при повторном запуске. Для Linux/macOS использовать .venv/bin/python.
Токен Kaspi и ключ приложения читаются из локального .env; переменные окружения имеют приоритет.
Путь к базе можно задать KASPIBOT_DATABASE=абсолютный-путь-к-app.db.
DATABASE_URL/SQLALCHEMY_DATABASE_URI для этой однофайловой версии не использовать.

## Функции

Главные страницы из архива: /products, /incoming, /stock-in, /onway, /import,
/orders, /report, /chat, /products/parser/. Меню «Каталог и пороги» открывает
перенесённые функции на этих же карточках: SKU/категория/цена, пороги,
архив/восстановление. История цены, журнал, сводка и роли — администратору.
Цены с сотыми хранятся в новом price_cents и синхронизируются с current_price.
Изменения из обоих интерфейсов пишутся в audit_log; фоновые — от system.
Удаление через API архивирует карточку и сохраняет связи, aliases и движения.
Исходные59пустыхSKU не заполнялись искусственно; для новых ручных карточек SKU обязателен.
Учебные заказы — /management/orders, новая demo_orders в той же базе;
KaspiOrderSync не переименован, учебные и реальные продажи не смешиваются.

## Бесплатный запуск и внешние функции

Склад, Excel, отчёты и справочная помощь работают без ключей, подписок и карты.
Bootstrap сохранён локально с лицензией. Для разрешённого Kaspi API задать
KASPI_API_TOKEN. Для парсера: python -m playwright install chromium.
AI_PROVIDER=off по умолчанию. Локальный ИИ: AI_PROVIDER=ollama и OLLAMA_MODEL
уже установленной модели. Gemini: AI_PROVIDER=gemini, GEMINI_API_KEY и доступный
GEMINI_MODEL; условия бесплатного API зависят от аккаунта. ИИ не имеет инструментов
записи в базу. Не вводить персональные данные в сообщения внешнему ИИ.

## Сохранность и копии

На копии настоящей базы дважды выполнен init-db; сравнены **все исходные поля
каждой строки9таблиц**, не только количества. Проверка сразу после миграции:
python tools/verify_preservation.py backups/archive-original.db app.db.
После ваших рабочих изменений расхождения с оригиналом ожидаемы.
backup-db сохраняет теперь всю app.db. Автокопии: python tools/backup_archive.py
--database app.db --output backups --interval-seconds 86400; хранение30дней,
пока процесс работает. Это RPO до24ч; целевые15мин требуют интервала≤900сек и места.
Восстановление — docs/course/acceptance-and-operations.md.

## Docker

Docker в этой среде не установлен: конфигурация подготовлена, реальная сборка не проверена.
Скопировать .env.example в .env, задать KSM_SECRET_KEY. База не включена в image.

```sh
docker compose build
docker compose run --rm -v "${PWD}:/import:ro" web python -c "import shutil; shutil.copyfile('/import/app.db','/app/instance/app.db')"
docker compose run --rm web flask --app app:create_app init-db
docker compose run --rm web flask --app app:create_app create-user --role admin admin
docker compose up -d
docker compose --profile operations up -d
```

Последняя команда включает backup и Uptime Kuma на localhost:3001.
В Kuma создать HTTP-монитор http://web:5000/health. Это не доказательство месячного SLO.

## Проверки и курс

python -m unittest discover -s tests -v; ruff check .; python -m pip check.
python tools/ui_smoke.py — Chromium на отдельной синтетической базе.
UI проверен на Chromium134; это не подтверждение последних двух Chrome/Edge.
Нагрузка — tests/locustfile.py, зависимости requirements-load.txt;
не запускать нагрузочные проверки на рабочей базе.
Карта всех12ЛЗ, финмодель команды2человека, notebook и оставшиеся реальные
мероприятия — [docs/course/README.md](course/README.md).
Интервью, независимая UAT, planning poker, новые2спринта и LLM-eval не выдуманы.
