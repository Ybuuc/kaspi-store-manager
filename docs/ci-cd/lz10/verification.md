# Проверка ЛЗ 10

Дата: 03.11.2026. PR: https://github.com/Ybuuc/kaspi-store-manager/pull/62.

## Локально

- Ruff: проходит.
- unittest: **29 tests, OK** (23 проверки приложения + 6 проверок расчёта DORA).
- Сборка ZIP: проходит; в архиве приложение, шаблоны, стили, lock и манифест.
- Повторное построение дашборда из сохранённого GitHub snapshot: проходит.
- Дашборд визуально проверен, значения совпадают с `reports/metrics.json`.

## GitHub Actions

Первый успешный запуск ветки:
https://github.com/Ybuuc/kaspi-store-manager/actions/runs/41616828653.

Успешный запуск PR:
https://github.com/Ybuuc/kaspi-store-manager/actions/runs/41616867055.

Все четыре jobs — **success**: `lint`, `test`, `dependencies`, `build`.
Trivy проверяет зафиксированные production-зависимости, HIGH/CRITICAL блокируют сборку.
Smoke распакованного ZIP выполнен в чистом временном каталоге на Linux runner.

Опубликованы `dependency-scan` (JSON Trivy) и `kaspi-store-manager-<SHA>` (ZIP + SHA-256).
GitHub Actions хранит пакет 30 дней, поэтому для сдачи сохранена также локальная копия.
Артефакт PR привязан к временному merge SHA GitHub; для релиза использовать пакет
успешного запуска на точном релизном SHA основной ветки.

В базовом отчёте DORA эти новые CI runs отсутствуют намеренно:
его окно заканчивается 03.11.2026 00:00 UTC. Новый экспорт обновляет окно.

## Что остаётся организационно

Защита main **сохранена и проверена** после подтверждения владельцем доступа GitHub.
Правило: https://github.com/Ybuuc/kaspi-store-manager/settings/branch_protection_rules/85743372.
Требуется 1 approve, сброс stale approve, разрешение обсуждений, актуальная main,
четыре проверки GitHub Actions (`lint`, `test`, `dependencies`, `build`).
Обход администратором, force push и удаление main запрещены.

PR требует независимого reviewer. Расписание DORA начнёт работать после merge в main;
локальный экспорт и построение уже проверены на настоящем GitHub API.
Для LT/CFR/MTTR нужна настоящая история production и incidents. Придумывать её,
записывать CI как production или создавать фиктивные сбои нельзя.
