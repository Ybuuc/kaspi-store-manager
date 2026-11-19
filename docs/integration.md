# Объединение Kaspi Store Manager и kaspibot

## Результат и архитектура

Один репозиторий, Flask factory `app:create_app`, процесс сервера, вход и сессия.
Исходный Store Manager работает на прежних адресах. Функции архива доступны
администратору через «Kaspi / AI / Excel» и `/bot/`. Это модульное объединение:
две схемы учёта сохранены в отдельных SQLite-файлах, а не превращены в одну таблицу.

| Область | Store Manager | kaspibot из архива | Решение |
|---|---|---|---|
| Запуск | Flask factory | глобальный Flask, миграции при импорте | Blueprint, единая factory, явная CLI-миграция |
| Доступ | users, admin/employee, блокировка входа | нет общей авторизации | существующий вход; весь `/bot` только admin |
| Данные | sqlite3, products/orders/audit_log | Flask-SQLAlchemy, products, движения, aliases, KaspiOrderSync | разные файлы, защита от совпадения пути |
| Цена | целые сотые тенге `price_cents` | целые тенге, закуп/продажа/комиссия | явная конвертация в мосте; дробные тенге отклоняются |
| Остаток | stock, архив, порог | stock_qty, reserved_qty, onway_qty, incoming | отдельные регистры; существующие остатки не перезаписываются |
| Заказы | учебные, утверждённые переходы статусов | реальные внешние коды, позиции, возвраты | учебные и внешние заказы не смешиваются |
| Имена | app.py, products/orders, base.html | те же имена | пакет kaspibot, URL `/bot`, шаблоны `kaspibot/` |
| Зависимости | Flask 3.1.3 | Flask 3.0.3, SQLAlchemy, openpyxl, Gemini, requests, Playwright | Flask 3.1.3; совместимый полный requirements.lock |
| Парсер | отсутствует | отдельный Python worker, Chromium | запуск `python -m kaspibot.parser_worker`, передача URI базы |
| Поставка | исходный ZIP и CI | Docker | общий Docker/Compose и расширенный release ZIP |

Сохранены каталог, архив, пороги, роли, цены/история, учебные заказы, отчёты,
audit_log и backup-db менеджера. Из архива перенесены склад, aliases,
ожидаемые поступления, приёмка, товары в пути, Excel-импорт, импорт Algatop JSON,
Kaspi API и карточки заказов, возвраты, отчёты прибыли/Excel, JSON API, Gemini
и локальные ответы чата, парсер цен с историей и фоновыми заданиями.

Исправлены обнаруженные ошибки архива: создание товара через JSON API пыталось
преобразовать SQLAlchemy-атрибут класса в int; отсутствовали шаблоны архива заказов
и возвратов; мёртвый код ссылался на неопределённый rows. Миграция теперь сообщает
об ошибке, вместо тихого продолжения с несовместимой схемой.

## Запуск в PowerShell

Нужен Python 3.12. Выполнять из корня репозитория:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
$env:KSM_SECRET_KEY = (.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db
.\.venv\Scripts\python.exe -m flask --app app:create_app init-bot-db
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user --role admin admin
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

Открыть http://127.0.0.1:5000, войти как admin. Ссылка «Kaspi / AI / Excel»
открывает расширенный раздел. Для Linux/macOS использовать `python3` и `.venv/bin/python`.
При повторном запуске задать тот же KSM_SECRET_KEY и выполнить последнюю команду.
Миграции выполнять после обновления; они не запускаются при импорте приложения.

Дополнительные подключения (в том же терминале перед запуском):

```powershell
$env:KASPI_API_TOKEN = "ваш токен"
$env:GEMINI_API_KEY = "ваш ключ"
$env:GEMINI_MODEL = "имя доступной вам модели"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Без ключей работают склад, Excel, отчёты и локальные ответы чата о складе.
Gemini требует ключ и доступную вашему аккаунту модель. Парсер требует Chromium
и сетевой доступ; настройки магазина задаются на его странице. Наличие интерфейса
не подтверждает доступ к реальному кабинету Kaspi.

## Данные и перенос каталога

По умолчанию: `instance/kaspi-store-manager.sqlite3` и `instance/kaspibot.sqlite3`.
Архивные `.env`, `.git`, окружение и `app.db` в GitHub не публикуются.
Для использования старого `app.db` остановить оба старых сервера, сделать резервную
копию, скопировать базу в `instance/kaspibot.sqlite3` и выполнить `init-bot-db`.
Так сохраняются исходные идентификаторы, движения, aliases и история импорта.
Нельзя копировать `app.db` на место базы менеджера.
Альтернатива — задать `KASPIBOT_DATABASE_URL=sqlite:///C:/absolute/path/app.db`
или абсолютный POSIX-путь. Настройка DATABASE_URL из старого проекта заменена
на KASPIBOT_DATABASE_URL, чтобы не перепутать две базы.

```powershell
# Сначала просмотреть новые товары и конфликты:
.\.venv\Scripts\python.exe -m flask --app app:create_app transfer-catalogue
# Затем явно применить:
.\.venv\Scripts\python.exe -m flask --app app:create_app transfer-catalogue --apply
```

Мост создаёт только новые товары по SKU без учёта регистра, переносит название,
категорию, цену продажи и начальный stock. Закупочная цена остаётся нулевой:
заполните её в расширенном разделе перед расчётом прибыли. Повторный перенос
пропускает существующий SKU и сохраняет его остаток, резервы, цены и движения.
Конфликт имени, неоднозначный SKU или дробные тенге отменяют всю операцию.
Архивированные товары не переносятся. Автоматической синхронизации в обратную
сторону нет. После начального переноса вести реальные операции в `/bot`;
отчёты двух регистров не суммировать, это может дважды учесть начальный склад.

`backup-db` сохраняет только базу менеджера. Для полной копии остановить сервер
и worker, сохранить оба SQLite-файла и ключ приложения. Перед возвратом к старой
версии восстановить соответствующие копии; не выполнять обратные ALTER TABLE.

## Docker

Скопировать `.env.example` в `.env`, заполнить KSM_SECRET_KEY и при необходимости ключи API.
Compose читает `.env`; локальный Flask сам этот файл не загружает.

```sh
docker compose build
docker compose run --rm web flask --app app:create_app init-db
docker compose run --rm web flask --app app:create_app init-bot-db
docker compose run --rm web flask --app app:create_app create-user --role admin admin
docker compose up -d
```

Базы сохраняются в Docker volume app-data. Сервер доступен на localhost:5000.
Chromium устанавливается при сборке; worker использует то же окружение.

## Проверки и границы

```sh
python -m unittest discover -s tests -v
python -m pip check
ruff check .
python tools/build_release.py
```

Тесты проверяют исходный менеджер, общий вход/роли/CSRF, страницы и ссылки,
создание товара и движение в пути, Excel-чтение/выгрузку, локальный чат,
mock-ответы Kaspi и Gemini, запись результатов парсера, импорт worker в отдельном
процессе, preview/apply/повтор моста, отклонение конфликтов и разделение баз.
Mock-проверки не подтверждают реальный обмен с Kaspi/Gemini.

Проверка 19 ноября 2026: **37 тестов проходят**, `ruff check .`, `pip check`
и `git diff --check` проходят. На временной копии архивного app.db дважды
выполнена миграция: число записей во всех 9 исходных таблицах сохранено,
`PRAGMA integrity_check` возвращает `ok`. Исходный архив SHA-256:
`98059986aca1255e70502694543ebc21a3be61ae21ba9a63b4057e2b052dbd14`.
Базовый commit менеджера: `4e277e1395a4707b9f4b025bc81ad0bd21761b0a`.
Docker в среде проверки отсутствует; сборка контейнера и реальный Chromium,
авторизованный кабинет Kaspi и внешний Gemini не проверялись.
Проверен также запуск из распакованного release ZIP: создание обеих баз,
создание администратора, вход и открытие каталога `/bot/products`.

Коллекции товаров и заказов читаются постранично до завершения; повтор страницы
или превышение лимита в 1000 страниц останавливают импорт. Заказы запрашиваются
за последние 14 дней; полнота вложенных позиций зависит от ответа entries API.
Комиссия 12% и тарифы доставки — правила исходного архива, их актуальность
не проверялась. Реальные продажи не входят в учебный отчёт менеджера.
JSON API перенесён под `/bot/api/*` и требует сеанса admin и CSRF-заголовка
`X-CSRF-Token`; старый мобильный клиент без авторизации потребуется обновить.
Сохранены устаревающие вызовы Query.get и datetime.utcnow; это предупреждения,
а не ошибки тестов. Нет общего audit_log для всех действий архива.
Для публичного размещения потребуются HTTPS и отдельная эксплуатационная настройка.
