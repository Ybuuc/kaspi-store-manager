# Реальные доставки Kaspi Store Manager на ноутбук и DORA

Для владельца Ybuuc. Windows, PowerShell, Python 3.12. Подготовлено 03.11.2026.
Репозиторий: https://github.com/Ybuuc/kaspi-store-manager

## Что здесь считается доставкой

Целевой рабочий экземпляр — **учебная установка на вашем ноутбуке**, которой вы
действительно пользуетесь и проводите приёмку. В GitHub её окружение назовём
`production`, потому что именно это имя собирает текущий DORA-скрипт.
Это локальная учебная эксплуатация, не действующий магазин с интеграцией Kaspi.
Отдельные тестовые копии и rollback-drill в это окружение не включаются.

Доставка — установка проверенного пакета определённого SHA на этот экземпляр,
запуск и приёмка. Создание deployment через API только записывает событие:
сам GitHub приложение на ноутбук не устанавливает.
Выполненные пункты чек-листа записывайте при каждом настоящем обновлении.

## 1. Предварительные условия

1. Независимый участник проверил PR №62, одобрил его; PR слит в main.
2. CI на main после merge завершился успешно.
3. На ноутбуке установлены Python 3.12, Git и официальный GitHub CLI.
   Если gh отсутствует, инструкция установки: https://github.com/cli/cli#installation.
   Для Windows с установленным winget:

```powershell
winget install --id GitHub.cli --exact
```

После установки открыть новое окно PowerShell:

```powershell
python --version
git --version
gh --version
gh auth login --hostname github.com --web --git-protocol https
gh auth status
```

Войти своим аккаунтом Ybuuc через браузер. Пароли и токены не вставлять в файлы
проекта. При 403 проверить аккаунт, права записи в репозиторий и разрешение
Deployments: write, если используете fine-grained token. Публичность репозитория
сама по себе не даёт права создавать deployment.

## 2. Постоянные каталоги

В управляющем окне PowerShell:

```powershell
$repo = 'Ybuuc/kaspi-store-manager'
$runtime = Join-Path $env:LOCALAPPDATA 'KaspiStoreManager'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'releases') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'backups') | Out-Null
```

Каждый SHA имеет отдельный каталог releases. Предыдущий каталог и его окружение
сохраняются для отката. Приложение не публикуется в интернет и не требует открытия
портов роутера. Веб-адрес на этом ноутбуке: http://127.0.0.1:5000.

## 3. Выбрать и скачать проверенный пакет

```powershell
gh run list --repo $repo --workflow ci.yml --branch main --event push --status success --limit 5
$runId = Read-Host 'Введите ID нужного успешного CI запуска на main'
$run = gh run view $runId --repo $repo --json headSha,headBranch,event,status,conclusion | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать CI run' }
if ($run.headBranch -ne 'main' -or $run.event -ne 'push' -or $run.status -ne 'completed' -or $run.conclusion -ne 'success') {
    throw 'Нужен успешный завершённый push-запуск на main'
}
$releaseSha = $run.headSha
$releaseDir = Join-Path (Join-Path $runtime 'releases') $releaseSha
if (Test-Path -LiteralPath $releaseDir) { throw 'Этот пакет уже распакован. Не перезаписывайте действующую установку.' }
New-Item -ItemType Directory -Path $releaseDir | Out-Null
$downloadDir = Join-Path $releaseDir 'download'
gh run download $runId --repo $repo --name "kaspi-store-manager-$releaseSha" --dir $downloadDir
if ($LASTEXITCODE -ne 0) { throw 'Не удалось скачать артефакт' }
$zip = Get-ChildItem -LiteralPath $downloadDir -Filter '*.zip' | Select-Object -First 1
$sum = Get-ChildItem -LiteralPath $downloadDir -Filter '*.zip.sha256' | Select-Object -First 1
if (-not $zip -or -not $sum) { throw 'Нет ZIP или контрольной суммы' }
$expected = ((Get-Content -LiteralPath $sum.FullName -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath $zip.FullName -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'Контрольная сумма ZIP не совпала' }
Expand-Archive -LiteralPath $zip.FullName -DestinationPath $releaseDir
$manifest = Get-Content -LiteralPath (Join-Path $releaseDir 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.commit -ne $releaseSha) { throw 'SHA манифеста не совпал с CI' }
Set-Location -LiteralPath $releaseDir
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Ошибка создания Python окружения' }
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Ошибка установки зависимостей' }
```

Если артефакт истёк, запустить CI на нужном SHA повторно и дождаться успеха.
Не выбирать пакет PR вместо main. Для очередного выпуска должны быть настоящие
изменения и новый SHA, прошедший review и CI; повторный запуск старого процесса
сам по себе не новый релиз.

## 4. Один постоянный ключ сеансов

В первом управляющем окне после установки окружения:

```powershell
$secretFile = Join-Path $runtime 'session-key.dpapi'
if (-not (Test-Path -LiteralPath $secretFile)) {
    $newKey = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
    $newKey | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $secretFile
    $newKey = $null
}
$secureKey = Get-Content -LiteralPath $secretFile | ConvertTo-SecureString
$env:KSM_SECRET_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
```

Файл ключа зашифрован средствами Windows для текущего пользователя. Он находится
вне Git; его не отправляют в PR и не включают в архив. При переустановке Windows
или смене пользователя потребуется отдельно решить перенос ключа.

## 5. Подготовить данные: первый запуск или обновление

**Первый запуск с новой учебной базой:**

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user owner --role admin
```

Команда запросит новый пароль пользователя приложения. Это отдельный пароль,
не пароль GitHub. Не запускать сценарий с пустой БД вместо существующей базы,
если в прежней установке уже есть нужные товары и заказы.

**При обновлении действующей установки:**

1. В старом окне сервера нажать Ctrl+C, временно прекратить ввод заказов.
2. В старом каталоге со старым ключом выполнить `backup-db` — точные команды ниже.
3. Убедиться, что команда вывела Backup created и создала новый файл.
4. Скопировать старую `instance/kaspi-store-manager.sqlite3` в `instance` нового
   каталога при остановленном сервере. Старый файл не удалять.
5. Использовать прежний session-key.dpapi; не создавать заново пользователя owner.
6. При изменении схемы сначала проверить обновление на копии; не запускать
   необратимую миграцию без отдельного плана восстановления.

Пример копирования, где старый каталог выбираете сами:

```powershell
$oldReleaseDir = Read-Host 'Полный путь к предыдущей рабочей установке'
Push-Location -LiteralPath $oldReleaseDir
try {
    .\.venv\Scripts\python.exe -m flask --app app:create_app backup-db (Join-Path $runtime 'backups')
    if ($LASTEXITCODE -ne 0) { throw 'Backup не выполнен. Обновление остановлено.' }
} finally {
    Pop-Location
}
New-Item -ItemType Directory -Force -Path (Join-Path $releaseDir 'instance') | Out-Null
Copy-Item -LiteralPath (Join-Path $oldReleaseDir 'instance/kaspi-store-manager.sqlite3') -Destination (Join-Path $releaseDir 'instance/kaspi-store-manager.sqlite3')
```

## 6. Начать регистрацию доставки

В управляющем окне, где сохранены `$repo`, `$releaseSha`, `$runId`, `$runtime`:

```powershell
$requestFile = Join-Path $runtime 'deployment-request.json'
$requestJson = @{ ref=$releaseSha; environment='production'; auto_merge=$false; production_environment=$true; description='Laptop educational working installation' } | ConvertTo-Json
[IO.File]::WriteAllText($requestFile, $requestJson, [Text.UTF8Encoding]::new($false))
$deployment = gh api --method POST "repos/$repo/deployments" --input $requestFile | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $deployment.id) { throw 'Deployment не создан: остановитесь и проверьте ответ API' }
$deploymentId = $deployment.id
gh api --method POST "repos/$repo/deployments/$deploymentId/statuses" -f state=in_progress -f description='Installing on laptop'
if ($LASTEXITCODE -ne 0) { throw 'Не удалось записать in_progress' }
$deploymentId | Set-Content -LiteralPath (Join-Path $releaseDir 'deployment-id.txt')
```

Не отключать проверки ради обхода ответа API 409/422. Проверить статус exact SHA,
ошибку API и корректность выбранного run. Создавать deployment нужно один раз
на попытку доставки, а его состояние обновлять отдельными statuses.

## 7. Запустить приложение и проверить

В этом же окне из нового каталога:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app run --host 127.0.0.1 --port 5000
```

Окно остаётся открытым — это сервер локальной учебной установки. Закрытие окна
или выключение ноутбука останавливает приложение. Эта команда не предназначена
для публичного интернет-сервера.

В браузере открыть http://127.0.0.1:5000 и проверить:

- [ ] Страница входа открывается; без входа каталог недоступен.
- [ ] Вход своей учётной записью работает.
- [ ] Видны нужные товары, цены и остатки, поиск находит товар.
- [ ] Заказы и их статусы доступны; контрольные количества совпали с прежней базой.
- [ ] Разрешённое изменение статуса тестового заказа выполняется.
- [ ] Logout завершает сеанс.
- [ ] После приёмки нет ошибок в течение 15 минут обычного использования.

## 8. Завершить запись доставки

Пока сервер работает, открыть **второе** окно PowerShell. Ввести:

```powershell
$repo = 'Ybuuc/kaspi-store-manager'
$runtime = Join-Path $env:LOCALAPPDATA 'KaspiStoreManager'
$releaseSha = Read-Host 'Полный SHA действительно запущенного выпуска'
$releaseDir = Join-Path (Join-Path $runtime 'releases') $releaseSha
$deploymentId = (Get-Content -LiteralPath (Join-Path $releaseDir 'deployment-id.txt') -Raw).Trim()
gh api --method POST "repos/$repo/deployments/$deploymentId/statuses" -f state=success -f description='Laptop acceptance passed'
if ($LASTEXITCODE -ne 0) { throw 'Success не записан: проверьте ответ API' }
$releaseDir | Set-Content -LiteralPath (Join-Path $runtime 'active-path.txt')
```

Success ставить только после выполненной приёмки. Если установка не заработала,
вместо success записать `-f state=failure` с коротким фактическим описанием,
выполнить откат по release-plan.md и оформить запись причины.

Проверить запись:

```powershell
gh api "repos/$repo/deployments/$deploymentId/statuses" --jq '.[] | {state,created_at,description}'
```

GitHub сохраняет SHA в deployment, время результата — в created_at status.
Не регистрировать старые доставки текущим временем и не дублировать success для
искусственного увеличения DF. При первом успешном выпуске сохранить deployment ID:
он будет baseline для последующих изменений.

## 9. Если произошёл настоящий сбой

Подготовленный incidents.json сейчас пуст — журнал ещё нужно наполнять реальными
событиями. Учебная тренировка в него не добавляется.

1. При обнаружении создать issue с названием `Incident: <краткое описание>`.
   Предварительно владелец создаёт label `incident` через Issues → Labels → New label.
2. В issue указать deployment ID, полный SHA, симптомы, время обнаружения UTC,
   влияние и план восстановления. Issue создавать сразу: текущий расчёт использует
   её created_at как момент обнаружения.
3. Выполнить исправление/откат, проверить данные и доступность.
4. Описать восстановление и закрыть issue сразу после подтверждения. Скрипт использует
   closed_at как время восстановления; позднее закрытие завысит MTTR.
5. В отдельной ветке добавить связь в `docs/ci-cd/lz10/data/incidents.json`, например
   `{"issue_number": 123, "deployment_id": 456}` — числа заменить фактическими ID.
6. Пройти PR/review для изменения журнала. Несколько incidents одного deployment
   увеличивают число incidents, но сам deployment в CFR считается один раз.

Если ничего не ломалось, не создавать фиктивный incident. При подтверждённом полном
учёте CFR может быть 0%, а MTTR остаётся N/A.

## 10. Обновить DORA

Из актуального исходного clone, содержащего инструменты ЛЗ10:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools/dora.py export
.\.venv\Scripts\python.exe tools/dora.py report
```

Исходный 90-дневный отчёт содержит первый deployment без baseline до начала окна,
поэтому строгий расчёт LT для такого окна остаётся N/A. Для анализа новых доставок
выбрать окно **после первого baseline**, используя новый аргумент `--start`:

```powershell
$windowStart = Read-Host 'UTC время чуть ПОСЛЕ первого success, например 2026-11-05T14:30:00.000001Z'
$windowEnd = [DateTimeOffset]::UtcNow.ToString('o')
.\.venv\Scripts\python.exe tools/dora.py export --start $windowStart --end $windowEnd
```

Дата в примере не является состоявшейся доставкой. Использовать реальное created_at
первого success, сдвинутое на микросекунду вперёд, и end после следующего success.
Первый deployment останется в выгрузке для сравнения SHA, но не войдёт в число
доставок выбранного окна. Должен появиться хотя бы один новый non-merge commit,
который действительно доставлен следующим релизом; повтор SHA не даёт LT-образец.

Перед report проверить `data/incidents.json`: start/end покрывают выбранное окно,
все известные incident внесены. Только если учёт действительно полный, установить
`complete: true`. Для честного первого периода не объявлять полными прежние месяцы,
в которых журнал не вёлся. Затем выполнить `tools/dora.py report`.

Результаты: `docs/ci-cd/lz10/reports/dashboard.png` и `metrics.json`.
Для постоянного автоматического расчёта после появления baseline нужно также
выбрать тот же старт периода в workflow DORA и поддерживать границы подтверждённого
журнала; по умолчанию workflow продолжает брать последние 90 дней.

## Первоисточники

- GitHub CLI: https://cli.github.com/manual/gh_api
- Загрузка артефактов: https://cli.github.com/manual/gh_run_download
- Создание deployment: https://docs.github.com/en/rest/deployments/deployments
- Статусы: https://docs.github.com/en/rest/deployments/statuses
