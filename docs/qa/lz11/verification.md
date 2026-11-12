# ЛЗ11 — журнал проверок (12.11.2026)

**Репозиторий:** `Ybuuc/kaspi-store-manager`. **Ветка:** `lz11-qa-uat-postmortem`. **Контрольный коммит с кодом/тестами и hardening:** `150e648df38d612805087890eae64d768a681e7f`.

| Проверка | Реальный статус | Подтверждение |
|---|---|---|
| pytest: исходный unittest suite + 4 интеграционных теста ЛЗ11 | **PASS: 27 тестов за 4,44 с** | [Job log / run #43274333617](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274333617) |
| Повторный pytest после non-root Dockerfile | **PASS** | [Run #43274923730](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923730) |
| Исходный `Application tests` | **PASS** | [Run #43274923670](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923670) |
| Trivy + Gitleaks | **PASS** после добавления непривилегированного `USER ksm` | [Run #43274923600](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923600) |
| Docker Compose / Uptime Kuma реально работают на стенде | **NOT RUN** | Требуется Docker Desktop и локальный запуск; issue #64 |
| Playwright GUI, Bruno HTTP, k6 200 VUs | **NOT RUN** | Требуются изолированный стенд, тестовые пользователи, браузер/k6 |
| UAT с независимой командой | **NOT RUN** | Нет сессии и подписи заказчика; issue #67 |

**История исправления:** на commit `3307570` Trivy завершился с ошибкой; в Dockerfile отсутствовала директива переключения на non-root пользователя. Изменён Dockerfile в `150e648`; повторный security workflow завершился успешно. Не делать выводов о production-безопасности только из зеленого CI.

**Принцип:** GitHub Actions подтверждают выполнение только указанных команд. Не считать тесты приемки и эксплуатационные проверки пройденными, пока не записаны отдельные фактические результаты.
