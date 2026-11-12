# ЛЗ11 — журнал проверок (12.11.2026)

**Репозиторий:** `Ybuuc/kaspi-store-manager`. **Ветка:** `lz11-qa-uat-postmortem`. **Контрольный коммит с кодом/тестами и hardening:** `150e648df38d612805087890eae64d768a681e7f`.

| Проверка | Реальный статус | Подтверждение |
|---|---|---|
| pytest: исходный unittest suite + 4 интеграционных теста ЛЗ11 | **PASS: 27 тестов за 4,44 с** | [Job log / run #43274333617](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274333617) |
| Повторный pytest после non-root Dockerfile | **PASS** | [Run #43274923730](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923730) |
| Исходный `Application tests` | **PASS** | [Run #43274923670](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923670) |
| Trivy + Gitleaks | **PASS** после добавления непривилегированного `USER ksm` | [Run #43274923600](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923600) |
| Docker Compose: Flask + Uptime Kuma v2, изолированная база и временные учётные записи | **PASS: контейнеры healthy, HTTP 200** | [Live QA run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094) |
| Playwright GUI (Chromium): вход Employee, каталог, Admin→403 | **PASS** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `playwright.xml`, `playwright-catalog.png` |
| Bruno (HTTP GET login→200, неизвестный маршрут→404) | **PASS** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `bruno.json` и `bruno.xml` |
| k6, 5 VU × 15 секунд на синтетическом локальном стенде | **PASS: 80/80 checks; p95 /products = 6,2 мс; 0% HTTP ошибок (75 запросов каталога)** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `k6-summary.json` |
| Uptime Kuma: монитор `http://app:5000/login` (интервал 20 с, только CI) | **PASS: UP → DOWN → UP**, отключение приложения контролируемое | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `uptime-kuma-drill.json`, 3 скриншота статусов |
| Отчёты всех инструментов и скриншоты | **9 файлов в ZIP**, retention 14 дней; без паролей | [Live QA evidence](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094/artifacts/13973270776) |
| Нагрузочный NFR-01 (200 пользователей, p95≤800 мс) | **NOT VERIFIED**: 5 VU smoke не заменяет 200 VU | [NFR](../../requirements/NFR.md) |
| Постоянный мониторинг локального ПК и оповещения | **NOT DEPLOYED**: CI-стенд одноразовый; ручной стенд по runbook отдельно | [Issue #64](https://github.com/Ybuuc/kaspi-store-manager/issues/64) |
| Независимый UAT с другой командой и подписанный протокол | **NOT RUN**: участники, дата и подпись не представлены | [Issue #67](https://github.com/Ybuuc/kaspi-store-manager/issues/67) |

**История исправления:** на commit `3307570` Trivy завершился с ошибкой; в Dockerfile отсутствовала директива переключения на non-root пользователя. Изменён Dockerfile в `150e648`; повторный security workflow завершился успешно. Не делать выводов о production-безопасности только из зеленого CI.

**Что можно считать выполненным в ЛЗ11:** технический запуск Docker и Uptime Kuma с фактическим мониторинговым циклом, реальный браузерный тест и HTTP-контракты, ограниченная нагрузка k6; всё подтверждено в одноразовом CI-сценарии. **Что нельзя считать выполненным:** UAT, принятый другой командой, эксплуатация на постоянном стенде и целевой 200-VU NFR.

**Принцип:** GitHub Actions подтверждают выполнение только указанных команд. Не считать тесты приемки и эксплуатационные проверки пройденными, пока не записаны отдельные фактические результаты.
