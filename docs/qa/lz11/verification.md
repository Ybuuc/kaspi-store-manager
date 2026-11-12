# ЛЗ11 — журнал проверок (12.11.2026)

**Репозиторий:** `Ybuuc/kaspi-store-manager`. **Ветка:** `lz11-qa-uat-postmortem`. **Контрольная практика:** изолированные GitHub Actions, все изменения в PR #68; артефакты ограничены сроком хранения CI.

| Проверка | Реальный статус | Подтверждение |
|---|---|---|
| pytest: 23 исходных + 4 integration + 10 технических UAT-preflight | **PASS: 37/37 за 6,26 с** | [Run #43290236331](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43290236331); это не независимая приемка |
| Повторный pytest после non-root Dockerfile | **PASS** | [Run #43274923730](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923730) |
| Исходный `Application tests` | **PASS** | [Run #43274923670](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923670) |
| Trivy + Gitleaks | **PASS** после добавления непривилегированного `USER ksm` | [Run #43274923600](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43274923600) |
| Docker Compose: Flask + Uptime Kuma v2, изолированная база и временные учётные записи | **PASS: контейнеры healthy, HTTP 200** | [Live QA run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094) |
| Playwright GUI (Chromium): вход Employee, каталог, Admin→403 | **PASS** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `playwright.xml`, `playwright-catalog.png` |
| Bruno (HTTP GET login→200, неизвестный маршрут→404) | **PASS** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `bruno.json` и `bruno.xml` |
| k6, 5 VU × 15 секунд на синтетическом локальном стенде | **PASS: 80/80 checks; p95 /products = 6,2 мс; 0% HTTP ошибок (75 запросов каталога)** | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `k6-summary.json` |
| Uptime Kuma: монитор `http://app:5000/login` (интервал 20 с, только CI) | **PASS: UP → DOWN → UP**, отключение приложения контролируемое | [Run #43280650094](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094); `uptime-kuma-drill.json`, 3 скриншота статусов |
| Отчёты всех инструментов и скриншоты | **9 файлов в ZIP**, retention 14 дней; без паролей | [Live QA evidence](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43280650094/artifacts/13973270776) |
| Нагрузочный NFR-01 (200 VU × 30 с, 100 синтетических SKU) | **PASS после оптимизации: p95 каталога = 22,4 мс; 5440/5440 checks; 0% ошибок**, Gunicorn 2×16 threads | [Run #43289082394](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43289082394); до оптимизации 1,6 с [run #43288545717](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43288545717) |
| Kuma HTTP-монитор 60 с и локальные webhook-оповещения | **PASS на отдельном CI-стенде:** UP→DOWN→UP, фактическая доставка уведомлений DOWN/UP в внутренний webhook; **NOT DEPLOYED** на постоянном ПК | [Run #43293030085](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43293030085), [evidence #13977488012](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43293030085/artifacts/13977488012), [#64](https://github.com/Ybuuc/kaspi-store-manager/issues/64) |
| Backup worker (отдельный том и интервал 15 минут, retention 30 дней) | **PASS: запуск и первая копия с restore / integrity**, длительная работа пока не подтверждена | [Run #43290704502](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43290704502); профиль `scheduled-backups` |
| Pre-release gate с 37 тестами и smoke-проверкой собранного пакета | **PASS**, build зависит от unit/integration, lint и dependency scan | [CI run #43290958703](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43290958703) |
| Откат реальной предыдущей учебной сборки при инъекции HTTP 503 | **PASS: rollback кода = 0,255 с; отдельный DB restore = 0,251 с; новые заказы не потеряны при откате кода** | [Run #43291242294](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43291242294); `release-rollback.json` |
| Windows PowerShell: запуск/автозапуск, rclone copy→check→download/SHA-256, Task Scheduler | **PASS: синтаксис и безопасный -ValidateOnly на hosted Windows CI; NOT RUN на ПК владельца** | [CI #43295745128](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43295745128), [инструкция](../../../ops/lz11/windows/README.md) |
| Независимая UAT с заказчиком другой команды | **NOT RUN** — подписи, представитель и дата отсутствуют | [Issue #67](https://github.com/Ybuuc/kaspi-store-manager/issues/67) |

**История исправления:** на commit `3307570` Trivy завершился с ошибкой; в Dockerfile отсутствовала директива переключения на non-root пользователя. Изменён Dockerfile в `150e648`; повторный security workflow завершился успешно. Не делать выводов о production-безопасности только из зеленого CI.

**Что можно считать выполненным в ЛЗ11:** запуск Docker/Uptime Kuma, Playwright, Bruno, k6 на 5 и 200 VU (после оптимизации), отдельный restore SQLite, тест фонового backup-worker, выпуск с обязательным pytest gate, проверенный release rollback. **Что нельзя считать выполненным:** UAT, принятый другой командой; непрерывная эксплуатация на компьютере пользователя, реальный внешний/offsite backup и 30-дневный цикл хранения.

**Принцип:** GitHub Actions подтверждают выполнение только указанных команд. Не считать тесты приемки и эксплуатационные проверки пройденными, пока не записаны отдельные фактические результаты.

**Готовится отдельное подтверждение новых синтетических данных:** versioned JSON `demo/lz11_seed.json` и автоматическая загрузка `seed-demo` в Docker без пользовательских паролей. Новый CI и реальный перезапуск на пользовательском ПК должны быть проверены отдельно; существующие результаты CI относятся к предыдущим коммитам.
