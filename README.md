# Kaspi Store Manager

Kaspi дүкенін басқаруға арналған оқу ақпараттық жүйесі.

## Жобаның мақсаты
Тауарларды, бағаларды, қойма қалдықтарын және тапсырыстарды
бір жүйеде есепке алуды ұйымдастыру.

## Жоспарланған мүмкіндіктер
- Тауарларды қосу, өзгерту, іздеу, архивке жіберу және қайтару.
- Бағалар мен қойма қалдықтарын есепке алу.
- Тапсырыстардың күйін бақылау.
- Сатылым бойынша есептерді қалыптастыру.

## Жеткізу моделі
Қысқа итерациялармен инкременттік жеткізу.
Тапсырмалар Kanban тақтасында басқарылады.

## Құжаттама
- [ADR-001: жеткізу моделін таңдау](docs/adr/ADR-001-delivery-model.md)
- Жұмыс тәртібі репозиторийдің Wiki бөлімінде сипатталады.

## Жобаның ағымдағы кезеңі
MVP Core функционалды бөлігі (US-01–US-08), әр тауарға жеке төмен қалдық шегі (US-09), тауар архиві (US-10), бағаны өзгерту (US-11), оқу тапсырыстар тізімі (US-13), қойма есебі (US-15) және рөлдерді басқару (US-17) іске асырылды. [MVP талаптары](docs/requirements/mvp.md) код пен тесттерге негіз болады. Өндірістік орналастыру және сыртқы интеграциялар бөлек кезеңдер.

## Как скачать и запустить ИС локально

Нужны Python **3.10 или новее**, доступ к интернету для установки зависимостей и, если выбран способ с `git clone`, Git. Система использует Flask и SQLite: отдельный сервер базы данных для локального запуска не требуется. Это локальная учебная версия; для работы с настоящими данными Kaspi интеграция пока не подключена.

### 1. Скачайте проект

**Через Git:** откройте PowerShell (Windows) или терминал (macOS/Linux) в папке, где хотите хранить проект, и выполните:

```sh
git clone https://github.com/Ybuuc/kaspi-store-manager.git
cd kaspi-store-manager
```

**Без Git:** на [странице репозитория](https://github.com/Ybuuc/kaspi-store-manager) выберите **Code → Download ZIP**, распакуйте архив и откройте терминал в распакованной папке с файлами `README.md`, `app.py` и `requirements.txt`. Все следующие команды выполняются в этой папке.

### 2. Создайте окружение и установите зависимости

Проверьте версию Python: в Windows `python --version`, в macOS/Linux `python3 --version`. Если версия ниже 3.10 или команда не найдена, сначала [установите Python](https://www.python.org/downloads/). В Windows при установке отметьте добавление Python в PATH.

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS/Linux:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Активация окружения не нужна: команды ниже обращаются прямо к его Python.

### 3. Задайте ключ приложения

Ключ нужен для защищённых сеансов входа. Создайте его в текущем окне терминала. Не публикуйте ключ и не добавляйте его в Git. При перезапуске в новом окне можно создать новый ключ той же командой: пользователям потребуется войти снова, но учётные записи и товары сохранятся.

**Windows PowerShell:**

```powershell
$env:KSM_SECRET_KEY = (.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
```

**macOS/Linux:**

```sh
export KSM_SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

### 4. Создайте базу и учётную запись

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user employee
```

**macOS/Linux:**

```sh
.venv/bin/python -m flask --app app:create_app init-db
.venv/bin/python -m flask --app app:create_app create-user employee
```

`create-user` попросит ввести и подтвердить пароль. Запомните имя `employee` и выбранный пароль: они нужны для входа. Для учётной записи администратора можно отдельно выполнить `create-user --role admin admin` с тем же префиксом команды Python и Flask. Команда `init-db` безопасна и для ранее созданной базы: она добавляет недостающие поля `category`, `role` и `archived_at`.

Для демонстрации списка заказов можно добавить **учебный** заказ (дата, сумма и статус в команде ниже — пример, не данные Kaspi):

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m flask --app app:create_app add-demo-order 2026-01-15 2500.00 "Жаңа"
```

```sh
# macOS/Linux
.venv/bin/python -m flask --app app:create_app add-demo-order 2026-01-15 2500.00 "Жаңа"
```

Статус для учебного заказа задаётся текстом; утверждённый порядок смены статусов пока не принят. Без этой команды раздел заказов покажет пустой список.

### 5. Запустите приложение и войдите

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

**macOS/Linux:**

```sh
.venv/bin/python -m flask --app app:create_app run
```

Откройте в браузере [http://127.0.0.1:5000](http://127.0.0.1:5000) и войдите с созданными именем пользователя и паролем. После входа откроется список товаров. Первый список пустой: добавьте товар через **«+ Жаңа тауар»**, откройте его карточку, измените цену или остаток либо отправьте его в архив. В карточке можно задать для этого товара **«Төмен қалдық шегі»**: когда остаток станет ниже заданного целого числа, предупреждение появится в карточке и списке. Если оставить поле пустым, предупреждение для товара отключится. Через пункт **«Тапсырыстар»** откройте учебный список заказов и фильтр статуса. Администратору также доступны **«Қойма есебі»** (активные товары и число товаров ниже индивидуального порога) и **«Әкімші»** (смена ролей сотрудников). Сотруднику эти разделы закрыты, в том числе по прямой ссылке. Для остановки сервера нажмите **Ctrl+C** в терминале.

### Повторный запуск, данные и проверка

При следующем запуске откройте терминал в папке проекта, повторите команду для `KSM_SECRET_KEY` из шага 3 и команду из шага 5. База данных находится в `instance/kaspi-store-manager.sqlite3` и сохраняется между запусками. Папка `instance/` не попадает в Git; сохраните её отдельно перед удалением проекта или переносом на другой компьютер. Повторно создавать пользователя не нужно. После обновления проекта выполните `init-db` ещё раз для миграции схемы.

Для проверки проекта запустите тесты:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```sh
# macOS/Linux
.venv/bin/python -m unittest discover -s tests -v
```

Если появляется сообщение `Set KSM_SECRET_KEY`, повторите шаг 3 в том же окне терминала. Если порт 5000 занят, остановите прежний запуск через **Ctrl+C** или добавьте `--port 5001` к команде запуска и откройте `http://127.0.0.1:5001`. Встроенный сервер Flask предназначен для локальной разработки; для публичного размещения потребуется отдельная настройка HTTPS и рабочего сервера, а также решение по хранению данных.

## Жұмыс кеңістігіне сілтемелер

- [GitHub Project](https://github.com/users/Ybuuc/projects/1)
- [Kanban тақтасы](https://github.com/users/Ybuuc/projects/1/views/2)
- [Жоба Wiki](https://github.com/Ybuuc/kaspi-store-manager/wiki)
- [Жұмыс тәртібі және атау ережелері](https://github.com/Ybuuc/kaspi-store-manager/wiki/Workflow)
- [ADR-001](docs/adr/ADR-001-delivery-model.md)


## ЛЗ 5 жоспарлау артефактілері
- [WBS және WBS сөздігі](docs/planning/lz5/WBS.md)
- [Семестрлік релиздер жоспары](docs/planning/lz5/release-plan.md)
- [Тәуелділіктер картасы](docs/planning/lz5/dependencies.md)
- [Team Topology, RACI және Capacity](docs/planning/lz5/team-raci-capacity.md)


## ЛЗ 7 — қауіптер және сәйкестік
- [RAID тәуекелдер журналы](docs/security/lz7/RAID-risk-register.md)
- [STRIDE Threat Model](docs/security/lz7/STRIDE-threat-model.md)
- [Сәйкестік чек-листі](docs/security/lz7/compliance-checklist.md)
- [Қауіпсіздік скандарының GitHub Actions workflow-ы](.github/workflows/security-scans.yml)

Ескерту: скан workflow-ы қосылды, бірақ нақты нәтижелер workflow орындалғаннан кейін тіркелуі тиіс.


## ЛЗ 9 — екі спринтті симуляциялау
- [Жұмыс процесі, DoR/DoD, спринт жоспары, ретро, метрика скрипті](docs/process/lz9/README.md)
