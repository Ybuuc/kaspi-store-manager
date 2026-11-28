# Fresh XML from the Kaspi seller cabinet

This optional source is read-only: it uses the existing cabinet command
**Управление товарами → Действия с файлами → Скачать прайс-лист в XML**.
It does not use the order API token and never uploads a price list or edits a
product. The export interface was observed in the real cabinet; it is not a
documented public Token API. Kaspi may change it or expire the login session.

The cabinet exposes authenticated export types/status/trigger operations under
`https://mc.shop.kaspi.kz/offers/api/v1/offer/export/`. They reject unauthenticated
reads with HTTP 401. No anonymous, permanently refreshed XML URL was configured
in this shop. The normal cabinet automatic-upload setting pulls data INTO Kaspi;
it is not an export to the application, and this implementation does not change it.

## Local setup

Use the existing Google Chrome installation (Playwright `channel=chrome`), without
installing a second Chromium distribution. One browser is started per scheduled
export and closed afterwards. Login uses a separate, visible, isolated context.
Your existing Chrome profile and the in-app browser's credentials are not copied.

```dotenv
KASPI_CATALOG_EXPORT=1
KASPI_CATALOG_MERCHANT_ID=YOUR_NUMERIC_MERCHANT_ID
KASPI_ALLOW_LOCAL_CATALOG=1
KASPI_CATALOG_URL=https://localhost:8443/catalog.xml
KASPI_CATALOG_CA_BUNDLE=.local-catalog/ca.pem
KASPI_AUTO_SYNC=1
KASPI_API_SYNC_INTERVAL_SECONDS=300
KASPI_XML_SYNC_INTERVAL_SECONDS=1800
KASPI_XML_MAX_BACKOFF_SECONDS=21600
```

From `C:\kaspibot`, with the local HTTPS server running:

```powershell
.\.venv\Scripts\python.exe tools\kaspi_catalog_login.py
```

Enter the password/SMS manually in the separate window. The helper stores only
the dedicated browser's session state in `.local-catalog/kaspi-session.json`,
protected by the folder's Windows ACL (current user and SYSTEM). Treat this file
like a password: keep it out of Git, Docker, screenshots and external uploads.
The helper validates the first real XML, publishes it and checks a real local
HTTPS read before importing. It reports readiness only after this succeeds.
Restart the web application after changing `.env`. Renew a rejected/expired
session by running the same helper; password and SMS automation is not used.

## Scheduled refresh and preservation

The existing SQLite lease covers export, publication and import in the same
XML cycle (30 minutes by default; API orders run independently every 5 minutes), so no second scheduler or Windows startup task is added.
Before publication, the full XML is validated and its merchant/date checked
against the last good database snapshot. A temporary file, fsync and same-folder
atomic replace prevent a partially written public catalogue. The exact bytes
served over local TLS must match the new export. If download/import fails, the
previous file is restored, unless an operator has already replaced it.
The API worker is queued independently even when source login/export fails.
Status distinguishes cabinet export, HTTPS access and XML import; a retained
old catalogue is not reported as a newly obtained store state.

The source uses only its own browser session. Scheduled mode uses an exact allowlist for observed read-only POST operations
(the getMerchant/getOrderCounters GraphQL queries, offer lists/filters and an
empty-body configuration read), plus the export-job trigger. GraphQL mutations,
configuration values and offer edits/uploads remain blocked. The export job
does not modify seller data. Refreshed session state, including IndexedDB, is saved atomically after the exact merchant is verified, before requesting the export, and again after success. An export error therefore does not discard a navigation-time session refresh. The login helper uses the same checkpoints. TLS/hostname checking stays on. Signed file links are never
logged, are followed without redirects, and are limited to the observed Kaspi
hosts. Unexpected responses/HTML, another merchant, old XML, large files and
timeouts preserve the previous successful catalogue.

A scheduled check is not a guarantee that Kaspi regenerates the export at that frequency. The source date and unchanged-file result remain visible;
the application never rewrites the XML date to pretend the contents are newer.
Original card identity, physical stock, costs, reserves and movements remain
separate from declared XML availability. Removed-from-sale products absent from
the ACTIVE export are retained in the database.


## Подтверждение новой выгрузки и ограничение Kaspi

Экспортёр проверяет HTTP-ответ операции `offer/export/trigger` после нажатия
«Скачать прайс-лист в XML». При 429 кабинет может всё равно скачать предыдущий
файл: такой XML не публикуется и не считается успешным свежим экспортом.
Статус показывает «лимит экспорта Kaspi», прежний снимок и его настоящая дата
остаются доступными. Повторная попытка выполняется по расписанию; 429 не
повторяется немедленно. HTTP 401 означает отклонение авторизации. HTTP 403 требует проверки доступа, а не автоматически нового входа; другие неуспешные ответы
также сохраняют прежний файл.

API заказов проверяется каждые 300 секунд, XML — каждые 1800 секунд при отсутствии серверного cooldown. Интервал означает частоту попыток. Получение нового снимка зависит
от доступности Kaspi и принятия запроса экспорта. Если Kaspi подтвердил запрос,
но вернул те же байты, дата снимка не подменяется текущим временем.

Открытый список товаров проверяет номер последнего импорта каждые 30 секунд
и обновляет карточки после появления нового XML. Поиск и раскрытые блоки учёта
сохраняются. Цена XML, заявленное количество и предзаказ показаны отдельно от
физического склада и закупочной цены; они не пересчитывают складской учёт.


## Диагностика загрузки кабинета

Обезличенный `.local-catalog/source-diagnostics.json` хранит только время,
категории зависимостей (авторизация, магазин, список товаров, экспорт) и HTTP-коды
или факт блокировки локальным фильтром. URL, параметры, тела запросов, токены и
контактные данные туда не записываются. Это локальный файл вне Git.

Ошибка до появления кнопки экспорта проверяется на HTTP 401, 403, 429 и блокировку
необходимого запроса фильтром. Тайм-аут без этих признаков не доказывает истечение
сессии. Служебные POST-запросы авторизации не разрешаются вслепую: сначала нужно
наблюсти и проверить конкретную операцию. Нельзя расширять фильтр до всех POST.
Серверный Retry-After учитывается и при 429 во время загрузки кабинета.

Сохранение IndexedDB устраняет неполноту снимка, но не доказывает, что именно
этот магазин использует IndexedDB для входа. sessionStorage по-прежнему не входит
в снимок. Если кабинет использует его для авторизации, потребуется отдельное
подтверждённое сохранение/восстановление этого состояния. Без длительной проверки
нельзя обещать бессрочную сессию или надёжность в процентах.


## Холодный запуск кабинета и бюджет ожидания

Кабинет загружает много модулей JavaScript. Наблюдавшаяся загрузка 165 ресурсов
показывала готовый интерфейс только после первоначального 30-секундного окна.
При этом сохранённая сессия оставалась действующей: повторный вход не требовался.
Навигация теперь ждёт `commit`, затем отдельно проверяет видимость экспортной
кнопки до 120 секунд внутри общего бюджета цикла 180 секунд. После этого по-прежнему
обязательны точный merchant ID, успешный trigger, проверка XML/TLS и импорт.
Бюджет готовности резервирует время для экспорта и учитывает короткие тестовые
циклы. Допустима одна ограниченная повторная попытка временного сбоя; 401/403/429
немедленно не повторяются. Сохранённая сессия повторно используется каждым циклом.

Диагностика сохраняет длительности стадий: навигация, готовность кабинета,
проверка магазина, ответ trigger и получение XML. Сетевые ошибки записываются
только как категории ERR_*, ошибки JavaScript — счётчиком; без содержимого
страницы, URL запросов или токенов. Штатное завершение ждёт XML до 200 секунд;
Docker/Gunicorn используют согласованные graceful-лимиты 210/220 секунд.


## Кэш публичных ресурсов

Read-only routing Playwright отключает встроенный HTTP-кэш браузера. Ранее каждый
новый контекст снова скачивал все модули кабинета. Экспортёр теперь сохраняет
только публичные версионированные `/mc/assets/*-hash.js` и `.css` с положительным
max-age. Ответы private/no-cache/no-store, Set-Cookie, Vary Cookie/Authorization,
URL с параметрами, HTML, variables.js, авторизация, API и XML никогда не кэшируются.
Проверяются срок и SHA-256; повреждение/истечение приводит к обычной загрузке.
Лимиты: 4 МиБ на файл, 64 МиБ / 256 файлов суммарно, максимум сутки в пределах
серверного max-age. Файлы лежат в приватной .local-catalog/asset-cache вне Git.
Изображения, шрифты и медиа фоновому экспортёру не нужны и не загружаются.
Service workers заблокированы, чтобы сетевые операции проходили через read-only
фильтр. Браузер и контекст закрываются после цикла; read-only allowlist сохраняется.

Диагностика показывает hits/saved кэша рядом с временем стадий. На текущем
кабинете повторная навигация использовала 95 кэшированных файлов и подтвердила
магазин за 5,83 секунды без нового входа. Это измерение одной проверки, не SLA.
Документация Playwright: https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route
