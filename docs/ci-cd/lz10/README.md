# ЛЗ 10. CI/CD и DORA — Kaspi Store Manager

Репозиторий: https://github.com/Ybuuc/kaspi-store-manager. Дата: 03.11.2026.
Публичный репозиторий: используется GitHub-hosted runner, платные BI-сервисы не нужны.

## Артефакты

- `.github/workflows/ci.yml`: линтер → тесты и Trivy → сборка → smoke-проверка распакованного приложения → публикация ZIP и SHA-256.
- `requirements.lock`: фиксированные production-зависимости Python 3.12.
- `tools/build_release.py`: архив приложения с шаблонами, стилями и манифестом SHA-256 каждого файла; БД и секреты исключены.
- `tools/dora.py`: постраничная выгрузка GitHub REST API, расчёт и matplotlib-дашборд.
- `data/github.json`: исходные ответы API с URL и временем получения. Это реальные данные, не демонстрационная выборка.
- `reports/dashboard.png`, `reports/metrics.json`: воспроизводимый результат.
- [Отчёт DORA](dora-report.md), [релизный план и откат](release-plan.md), [правила ревью](review-policy.md).

## Повторение

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock -r requirements-dev.txt
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python tools/build_release.py
# Воспроизвести сохранённый отчёт без сети:
.\.venv\Scripts\python tools/dora.py report
# Обновить данные за последние 90 дней:
.\.venv\Scripts\python tools/dora.py export
.\.venv\Scripts\python tools/dora.py report
```

Для точного воспроизведения базового окна: `export --days 90 --end 2026-11-03T00:00:00Z`.
Для периода после первой рабочей доставки можно явно задать `export --start <UTC> --end <UTC>`:
baseline должен быть до начала окна, чтобы определить изменения следующих доставок.
Токен необязателен для публичных данных, но GitHub ограничивает анонимные запросы.
При необходимости задать `GITHUB_TOKEN` в окружении; скрипт его не сохраняет.
Ошибки API останавливают экспорт: недоступные данные не превращаются в пустую историю.

Workflow `DORA dashboard` обновляет отчёт по понедельникам в 09:00 Asia/Qyzylorda
и вручную через Actions → Run workflow. Результаты доступны в artifact `dora-dashboard`.
Сохранённый базовый отчёт не перезаписывается в Git автоматически.

## Граница готовности

CI публикует проверенный пакет для ручной доставки. Настоящий production-сервер в проекте
не настроен; тестовый запуск на runner не регистрируется как production-deployment.
Для появления трёх недостающих DORA-метрик нужно начать регистрировать реальные
развёртывания и инциденты по процедуре релиза. Выполнить их задним числом по коммитам
нельзя. Лабораторная не заявляет вымышленные численные значения.

## Пошаговые инструкции

- [Независимому ревьюеру PR №62](instruction-reviewer-pr62.md).
- [Владельцу: рабочая установка на ноутбуке, Deployments и incidents](instruction-laptop-deployments.md).
