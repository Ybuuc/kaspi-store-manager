# ЛЗ №11 — Windows / Docker / Google Drive: фактическое развёртывание

**Область:** только синтетическая учебная среда Kaspi Store Manager, **не production**. Скрипты подготовлены для Windows 10/11 + Docker Desktop + rclone. Они существуют в GitHub и **не исполняются автоматически на компьютере пользователя**, пока пользователь лично не запустит их. Никаких прав к пользовательским данным, Kaspi и реальным заказам не требуется.

## 1. Зависимости

- Windows PowerShell 5.1, Docker Desktop (Linux containers), Git, rclone; все должны быть установлены и доступны через PATH.
- rclone remote с названием **gdrive:** уже должен работать в **этом Windows-аккаунте**. Проверка: `rclone lsd "gdrive:AI Projects"`.
- Достаточно компактной копии исходного кода (GitHub Branch `lz11-qa-uat-postmortem`). Нельзя выполнять на настоящей SQLite-базе продавца.
- Python 3.12, k6 и Playwright **на Windows не требуются** для обычной работы Docker стенда: они тестируются в GitHub CI.
- Не помещать в GitHub `rclone.conf`, пароли, файл SQLite и `%LOCALAPPDATA%\KaspiStoreManagerLZ11\secret.dpapi.txt`.

## 2. Запуск и автозапуск

Открыть PowerShell **под обычным пользователем**, в корне клонированного репозитория:

```powershell
git clone -b lz11-qa-uat-postmortem --single-branch --depth 1 https://github.com/Ybuuc/kaspi-store-manager.git
cd .\kaspi-store-manager
# Запуск сейчас; после входа в Windows стенд будет запускаться повторно.
.\ops\lz11\windows\Start-LZ11.ps1 -InstallAutoStart
# Если ExecutionPolicy запрещает скрипты, выполните только для текущего процесса:
# Set-ExecutionPolicy -Scope Process Bypass
```

При первом запуске создаётся случайный секрет, шифрованный **DPAPI текущего пользователя** и хранимый вне репозитория. Docker запускает Flask/Gunicorn, Uptime Kuma и 15-минутный `backup-worker`. Контейнеры работают, пока запущен Docker Desktop; **сон/выключение ПК прерывает мониторинг, расписание и резервирование**. Автозапуск в Startup folder выполняется при следующем интерактивном входе пользователя. Не удалять защищённый файл секрета, иначе прежние пользовательские Flask-сеансы станут невалидными.

Создать отдельных **учебных** пользователей (интерактивный ввод паролей):
```powershell
docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app create-user --role employee employee
docker compose -f ops/lz11/compose.yaml exec app python -m flask --app app:create_app create-user --role admin admin
```
Команда `init-db` уже выполнена скриптом; повторять создание тех же пользователей не требуется. Секреты **не передавать в чат и не помещать в GitHub**.

## 3. Uptime Kuma — одноразовая настройка с настоящим пользователем

Открыть `http://127.0.0.1:3001`, самостоятельно создать администратора, добавить HTTP monitor **KSM login** с адресом `http://app:5000/login`, expected **200**, интервалом **60 секунд**. Выбрать учебный канал доставки (без публикации токенов). Отдельно остановить и перезапустить `app` по runbook, приложить локальные скриншоты **UP→DOWN→UP** и фактически полученное уведомление. В CI это уже было проверено: [run #43293030085](https://github.com/Ybuuc/kaspi-store-manager/actions/runs/43293030085). На ПК это **ещё не подтверждено**. Порты 5000/3001 привязаны к 127.0.0.1, не открывать их в Интернет.

## 4. Offsite backup: Google Drive без удаления из облака

В той же учётной записи Windows:

```powershell
# Разовый экспорт из выделенного Docker volume → rclone copy → rclone check
# → обратно download одного файла и сравнение SHA256.
.\ops\lz11\windows\Export-Backups.ps1
# Затем включить расписание каждые 15 минут:
.\ops\lz11\windows\Install-OffsiteTask.ps1
# Диагностика:
Get-ScheduledTask -TaskName KaspiStoreManagerLZ11-OffsiteCopy
Get-Content "$env:LOCALAPPDATA\KaspiStoreManagerLZ11\last-offsite-check.json"
```

Назначение по умолчанию: **`gdrive:AI Projects/Kaspi-Store-Manager/Backups`**, его можно переопределить `-Remote "gdrive:другая/папка"`. Используется **только `rclone copy`**, не `sync`/ `delete`, поэтому существующие удалённые копии не удаляются. При каждом запуске **один уже скопированный** файл скачивается обратно во временную папку, SHA256 сверяется, временная папка удаляется. Самое важное: архивы внутри Docker именованного тома остаются неприкосновенными. На локальном Docker томе действует 30-дневная ротация, в Drive версии сохраняются до отдельной ручной политики хранения.

Скрипт расписания работает под **текущим вошедшим Windows пользователем**: требуются доступ Docker Desktop и rclone config. Нельзя обещать непрерывный RPO 15 минут при выключенном/спящем ноутбуке. Для чувствительных настоящих данных предпочтителен настроенный **rclone crypt** remote; данное решение рассчитано только на учебные синтетические записи.

Отключение без удаления данных:
```powershell
.\ops\lz11\windows\Install-OffsiteTask.ps1 -Remove
docker compose -f ops/lz11/compose.yaml down  # никогда не используйте down -v
```
Для удаления автозапуска вручную удалить **только** `$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\KaspiStoreManagerLZ11.cmd` после проверки пути. Это не удаляет Docker volumes.

## 5. Независимый UAT и окончательный отчёт

Другая реальная команда **сама** проверяет [UAT-01…UAT-10](../../../docs/qa/lz11/uat-scenarios.md), фиксирует участника/дату/commit, скриншоты и замечания в [протоколе](../../../docs/qa/lz11/uat-protocol.md) и выносит решение Accept / Conditional accept / Reject. **Никто не подставляет подписи и не отмечает PASS за отсутствовавших участников.** [Issue #67](https://github.com/Ybuuc/kaspi-store-manager/issues/67) остаётся открытой до реального выполнения. Технические 37/37 pytest не являются независимым UAT. Финальный Word собирается только после зафиксированной независимой приёмки.

## 6. Статус проверки подготовленных Windows-скриптов

CI `lz11-windows-scripts.yml` проверяет синтаксис PowerShell через Parser и режим `-ValidateOnly` на hosted Windows runner. **Это не развертывание на пользовательском ноутбуке и не доказательство успешной выгрузки в личный Google Drive.** Владелец фиксирует фактический запуск, время и файл `last-offsite-check.json` в [Issue #65](https://github.com/Ybuuc/kaspi-store-manager/issues/65).

### Двойной щелчок вместо ввода команд

Запустить `START_LZ11.cmd` в этой папке — старт Docker и автозапуск при входе пользователя; затем `OFFSITE_LZ11.cmd` — **проверка Google Drive путём обратного скачивания**, после успеха установка задачи на 15 минут. Если что-то не прошло, вторую часть нельзя считать выполненной. `OFFSITE_LZ11.cmd` специально не перезаписывает уже существующую задачу без решения пользователя.
