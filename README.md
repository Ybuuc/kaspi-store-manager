> Сверка по предоставленному XML, отдельные цены/предзаказ и оставшиеся SKU: [seller-xml.md](docs/seller-xml.md).

# Kaspi Stock — архив как основа

Приложение и app.db из kaspibot.zip — основа. Сохранены товары, aliases,
движения, поступления, товары в пути, Kaspi-заказы, импорт, отчёты, парсер и чат.
Функции менеджера добавлены в архив: **одна база, один каталог, один остаток**.

- [Исправление реальной синхронизации Kaspi](docs/kaspi-live-sync.md)
- [Запуск с данными и Docker](docs/integration.md)
- [Карта соответствия ЛЗ1–12](docs/course/README.md)
- [Финмодель команды2человека](docs/course/finance/README.md)
- [Монте-Карло на реальных GitHub issues](docs/course/lz6/README.md)
- [ADR-002: архив как основа](docs/adr/ADR-002-archive-first.md)

В локальном комплекте есть сохранённая app.db. В GitHub, CI и Docker image
данные магазина и API-ключи не публикуются. При скачивании кода скопировать
архивную app.db рядом с app.py перед init-db.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
$env:KSM_SECRET_KEY = (.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user --role admin admin
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

Вход: http://127.0.0.1:5000/management/login. Пароль задаётся вами.
Основные страницы архивные, новые функции — в меню «Каталог и пороги»,
«Архив товаров», «Сводка склада», «Журнал», «Сотрудники».
ИИ и внешние API необязательны; платных подписок для запуска нет.

Проверки: python -m unittest discover -s tests -v; ruff check .
Браузер: python -m playwright install chromium; python tools/ui_smoke.py.
Расчёты: pip install -r requirements-dev.txt; python tools/finance.py;
python tools/forecast.py; python tools/evaluate_assistant.py.
Исторические документы сохранены; актуальные оговорки — в docs/course.

Рабочий режим отделяет подтверждённые данные API от архивных остатков и расчётов.
Для физического склада нужна отдельная выгрузка кабинета; он не угадывается из истории заказов.
