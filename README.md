# Kaspi Store Manager

Kaspi дүкенін басқаруға арналған оқу ақпараттық жүйесі.

## Жобаның мақсаты
Тауарларды, бағаларды, қойма қалдықтарын және тапсырыстарды
бір жүйеде есепке алуды ұйымдастыру.

## Жоспарланған мүмкіндіктер
- Тауарларды қосу, өзгерту және іздеу.
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
MVP Core функционалды бөлігі (US-01–US-08) іске асырылды. [MVP талаптары](docs/requirements/mvp.md) код пен тесттерге негіз болады. Өндірістік орналастыру және сыртқы интеграциялар бөлек кезеңдер.

## Қолданбаны жергілікті іске қосу

Python 3.10+ қажет. `python -m venv .venv` жасап, ортаны іске қосыңыз, содан кейін:

```sh
pip install -r requirements.txt
export KSM_SECRET_KEY="replace-with-a-long-random-secret"
flask --app app:create_app init-db
flask --app app:create_app create-user employee
flask --app app:create_app add-product "Шай" TEA-1 1250.50 0
flask --app app:create_app run
```

Windows PowerShell-де `export` орнына `$env:KSM_SECRET_KEY = "..."` қолданыңыз. `create-user` құпиясөзді интерактивті сұрайды; әкімші тіркелгісі керек болса `create-user --role admin <username>` пайдаланыңыз. Қолданба `http://127.0.0.1:5000` адресінде ашылады. Өндірісте HTTPS прокси қажет және `KSM_HTTPS=1` орнатылуы тиіс. Дерекқор `instance/` ішінде сақталады; бұл қалта Git-ке қосылмайды. Бұрыннан бар дерекқор үшін жаңартудан кейін `init-db` қайта орындалуы тиіс — ол `category` және `role` бағандарын қосады. Тауарды веб-форма немесе CLI арқылы қосуға болады.

Тесттер: `python -m unittest discover -s tests -v`.
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
