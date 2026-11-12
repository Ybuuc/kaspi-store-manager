# ЛЗ №11 — план тестирования, UAT, postmortem и runbook

Проект: **Kaspi Store Manager**, учебное Flask/SQLite-приложение. Дата подготовки: **12.11.2026**. Используется только синтетическая база: реальное API Kaspi, кабинет продавца, реальные покупатели и production-стенд отсутствуют.

| Артефакт | Путь | Состояние |
|---|---|---|
| Стратегия и матрица проверок | [test-plan.md](test-plan.md) | Подготовлено; результаты запуска отдельно |
| 10 независимых UAT-сценариев | [uat-scenarios.md](uat-scenarios.md) | Готовы к исполнению |
| [Памятка независимому заказчику](uat-facilitator.md) | [uat-facilitator.md](uat-facilitator.md) | Подготовлено приглашение; команда ещё не согласована |\n| Протокол UAT | [uat-protocol.md](uat-protocol.md) | **Не проведён**: ожидается другая команда |
| Blameless postmortem | [postmortem.md](postmortem.md) | Учебный разбор **имитации** HTTP 503 от 03.11.2026 |
| Runbook отказа демо-стенда | [runbook.md](runbook.md) | Подготовлено; ручной прогон на новом стенде ожидается |
| Docker + Uptime Kuma | [../../../ops/lz11/compose.yaml](../../../ops/lz11/compose.yaml) | Конфигурация подготовлена; факт запуска пока не подтверждён |
| k6 / Bruno / Playwright | [../../../tests/lz11](../../../tests/lz11) | Тестовые сценарии, требующие реального запуска |

### Связь с бэклогом
- [#64 — Мониторинг/alert evidence](https://github.com/Ybuuc/kaspi-store-manager/issues/64), ответственный Ybuuc (DevOps), 16.11.2026.
- [#65 — Ежедневные backup/restore](https://github.com/Ybuuc/kaspi-store-manager/issues/65), ответственный Ybuuc (DBA), 18.11.2026.
- [#66 — Pre-release smoke gate](https://github.com/Ybuuc/kaspi-store-manager/issues/66), ответственный Ybuuc (QA/Release), 20.11.2026.
- [#67 — Независимая UAT-сессия](https://github.com/Ybuuc/kaspi-store-manager/issues/67), срок организации 19.11.2026.

**Важно:** наличие файлов, issue и автоматических проверок не равно подписанной приемке. Не следует заполнять результаты за другую команду или считать учебный отказ реальным производственным инцидентом.

## Подтвержденные автоматические проверки — 12.11.2026
- [pytest CI: 27 passed](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274333617) — 23 исходных + 4 новых теста.
- [Application tests: success](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923670).
- [Trivy + Gitleaks security scans: success после запуска контейнера от непривилегированного пользователя](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923600).
- [Полная ведомость фактов и ограничений](verification.md).

## Завершённая практика на GitHub runner — 12.11.2026

[Успешный live QA workflow](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094) проверил Flask + Kuma в Docker, Playwright, Bruno, k6 (5 VU, 15 с, 80/80 checks) и искусственную недоступность с восстановлением UP → DOWN → UP. [9 файлов-доказательств](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094/artifacts/13973270776) доступны как артефакт выполнения (до 26.11.2026); числа и ограничения сохранены в [verification.md](verification.md). **UAT другой командой не проводился** — [#67](https://github.com/Ybuuc/kaspi-store-manager/issues/67).
