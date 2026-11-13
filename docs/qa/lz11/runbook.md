# Runbook RB-01 — демо-стенд недоступен (HTTP 503 / Kuma DOWN)

**Область:** только локальный учебный стенд Docker Compose из `ops/lz11` и синтетические SQLite-данные. **Не применять для production**, где иные SLA, контакты и инфраструктура. **Ответственный за инцидент:** Ybuuc / оператор стенда. **Условие запуска:** Kuma DOWN 2 последовательных проверки либо `curl` страницы входа не возвращает 200.

## Перед запуском
- Python 3.12 и Docker Desktop установлен, доступ к `docker compose`. Запуск осуществлён командой из раздела «Мониторинг» ниже.
- Администратор имеет доступ к локальному журналу и отдельным backup SQLite **вне репозитория**.
- Пароль, SQLite и `.env` не публиковать. Время первого сигнала, commit SHA и screenshots фиксировать без секретов.

## Диагностика (0–5 минут)
1. Открыть `http://127.0.0.1:3001` → монитор **KSM login**; зафиксировать время DOWN. Проверить `http://127.0.0.1:5000/login`: ожидается HTTP **200**.
2. Из корня проекта: `docker compose -f ops/lz11/compose.yaml ps`; затем `docker compose -f ops/lz11/compose.yaml logs --tail=100 app`. Записать упавший контейнер, HTTP-код, последние безопасные строки лога.
3. Повторить из сети Compose: `docker compose -f ops/lz11/compose.yaml exec uptime-kuma ping -c 1 app` (если ping отсутствует, пропустить) и `docker compose -f ops/lz11/compose.yaml exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/login').status)"`.
4. Проверить свободное место и volume: `docker compose -f ops/lz11/compose.yaml exec app python -c "import os; print(os.statvfs('/app/instance').f_bavail)"`. Не выполнять `docker compose down -v`: это **удалит учебную БД и данные Kuma**.

## Восстановление (5–15 минут для учебной цели, не обещанный SLA)
1. Если проблема только в процессе приложения, перезапустить **только app**: `docker compose -f ops/lz11/compose.yaml restart app`.
2. Если откат кода необходим: записать текущий commit, создать каталог для снимков **в Docker volume** и сделать согласованную SQLite-копию командой приложения (можно при работающем app):
   ```powershell
   docker compose -f ops/lz11/compose.yaml exec app mkdir -p /app/instance/backups
   docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app backup-db /app/instance/backups
   New-Item -ItemType Directory -Force -Path ..\\ksm-backups
   docker compose -f ops/lz11/compose.yaml cp app:/app/instance/backups/. ../ksm-backups
   ```
   Проверить имя и целостность созданного файла; хранить копии **вне репозитория**. Команда backup-db требует предварительно существующий каталог, не использовать `docker compose down -v`.
3. Вернуться к заранее проверенному commit/release **через отдельную рабочую копию** и выполнить `docker compose ... up -d --build`. Не откатывать БД автоматически вместе с кодом: в ней могли появиться новые операции.
4. Если есть признаки SQLite corruption: остановить app, скопировать текущий файл БД как артефакт в безопасное место, восстановить последний **проверенный** backup в отдельной копии, выполнить `PRAGMA integrity_check`. Перед переключением сравнить заказы после времени backup (RPO), решение согласовать с владельцем данных. Не заменять базу «вслепую».
5. Smoke: `/login`=200, анонимный `/products`→302 на login, вход test employee, каталог→200, `/orders`→200; admin-путь под Employee→403. Зафиксировать продолжительность обнаружения/восстановления и точные timestamps.

## Завершение и коммуникация
- Убедиться, что Kuma снова UP минимум два интервала (по 60 сек), приложить скриншот события без приватных данных.
- Связаться с принимающей командой: «Учебный стенд был недоступен с __ по __; данные проверены; UAT продолжать/перенести».
- Создать issue с severity, временем, диагностикой, влиянием и ссылками на проверку. Для производственных инцидентов процедура и эскалация определяются отдельно.
- После восстановления провести blameless разбор: что помогло, какие проверки добавить, 3+ действия в backlog.

## Мониторинг локального стенда
```powershell
# Из корня репозитория. Укажите случайный секрет только в текущем сеансе:
$env:KSM_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
docker compose -f ops/lz11/compose.yaml up -d --build
docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app init-db
docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app create-user --role employee employee
docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app create-user --role admin admin
```
Затем открыть `http://127.0.0.1:3001`, создать администратора Kuma и HTTP-монитор `KSM login`: URL **`http://app:5000/login`**, expected status **200**, интервал **60 сек**, alert только в учебном канале. `localhost` *внутри* Kuma — не host-компьютер. Ожидаемая доступность страницы входа не гарантирует здоровье БД или бизнес-операций: после сбоя выполнять дополнительные smoke. Порты 5000 и 3001 привязаны только к `127.0.0.1`, наружу не выставлять.
```powershell
docker compose -f ops/lz11/compose.yaml ps
docker compose -f ops/lz11/compose.yaml down  # без -v, чтобы сохранить данные
```

## Автоматические резервные копии в отдельный volume (учебное окружение)

В `compose.yaml` есть дополнительный профиль `scheduled-backups`, выключенный по умолчанию. После создания учебной БД можно включить его из корня репозитория:

```powershell
docker compose -f ops/lz11/compose.yaml --profile scheduled-backups up -d --build
# Проверка работы сервиса и логов:
docker compose -f ops/lz11/compose.yaml logs --tail=30 backup-worker
```

Сервис каждые **900 секунд (15 минут)** выполняет согласованное SQLite online backup в отдельном именованном Docker volume `ksm_demo_backups`, восстанавливает снимок во временную БД для integrity/foreign-key и сверки контрольного отпечатка, затем удаляет собственные backup-файлы старше **30 суток**. На GitHub CI фактически проверяется старт и первая копия, но **не непрерывная 30-дневная работа**. Это не offsite backup: данные всё ещё на том же хосте. Для отказоустойчивости нужна дополнительная копия на внешний носитель или в Google Drive (например, rclone), и её фактическая проверка. Нельзя считать гарантированным RPO≤15 мин без измерения непрерывных циклов, задержек и восстановления после выключения компьютера. Никогда не применять `docker compose down -v` — это удалит БД и резервные копии.

## Windows и внешнее хранение (непроверенная на ПК установка)

Для пользовательского Windows стенда есть подготовленные скрипты: [инструкция](../../../ops/lz11/windows/README.md). Запуск через двойной щелчок `START_LZ11.cmd`, затем `OFFSITE_LZ11.cmd`: rclone `copy`, `check --one-way`, скачивание и SHA256. Это проверяется **при личном запуске владельцем**: до получения файла `last-offsite-check.json` нельзя утверждать, что копия в Google Drive существует. Система Task Scheduler требует работающую Windows-сессию; Docker Desktop работает только при включённом компьютере. Реальное постоянное оповещение в Uptime Kuma необходимо настроить в интерфейсе.
