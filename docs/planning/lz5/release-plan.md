# ЛЗ 5 — Семестрлік релиздер жоспары

Capacity қайта есептелгеннен кейін семестрге 65 сағаттық story scope және 3 сағат резерв жоспарланды.

| Инкремент | Мақсаты | Құрамы | Бағасы | Веха | Жоспарлы күні |
|---|---|---|---:|---|---|
| Increment 1 — MVP Core | Негізгі каталог және қойма сценарийін іске асыру | US-01–US-08 | 33 сағ | M1 — MVP Core Ready | 30.10.2026 |
| Increment 2 — Business Operations | Күнделікті операцияларды кеңейту | US-09, US-10, US-11, US-13 | 20 сағ | M2 — Operations Ready | 20.11.2026 |
| Increment 3 — Management & Control | Басқару және есептілік функцияларын қосу | US-15, US-17 | 12 сағ | M3 — Semester Release | 11.12.2026 |

## M1 — MVP Core Ready
- US-01–US-08 Acceptance Criteria орындалған.
- Critical defect жоқ.
- Негізгі positive және negative tests өткен.
- SKU unique және inventory validation тексерілген.
- GitHub Issues жаңартылған.

### Нақты орындалу мәртебесі — 01.11.2026

M1 жоспарлы күні 30.10.2026 болды. Нақты код [PR #40](https://github.com/Ybuuc/kaspi-store-manager/pull/40) (31.10.2026 19:35:19 UTC) және [PR #41](https://github.com/Ybuuc/kaspi-store-manager/pull/41) (31.10.2026 19:50:55 UTC) арқылы `main` тармағына біріктірілді. US-01–US-08 issues және [M1 issue #27](https://github.com/Ybuuc/kaspi-store-manager/issues/27) нақты біріктіруден кейін жабылды; Project #1 карточкалары `Done`. Application tests (10) және Security scans финалдық commit үшін сәтті аяқталды. Жергілікті Flask test client ішінде 100 тауар тізімі мен 10 000 тауар іздеуі Issue шектерінен жылдам болды; өндірістік жүктеме тексерісі жасалған жоқ.

Бұл мәртебе өндірістік орналастыруды, Kaspi API интеграциясын, backup/restore сынағын немесе PRIV-03 заңдық/retention бекітуін білдірмейді. Олар бөлек ашық тәуелділіктер болып қалады.

## M2 — Operations Ready
- Low-stock notification шектік мәнге сәйкес жұмыс істейді.
- Архивтеу/қалпына келтіру жұмыс істейді.
- Бағаны өзгерту сақталады.
- Тапсырыстар тізімі және status filtering жұмыс істейді.
- Regression tests өткен.

### Нақты орындалу мәртебесі

US-09 ([PR #48](https://github.com/Ybuuc/kaspi-store-manager/pull/48)), US-10 ([PR #43](https://github.com/Ybuuc/kaspi-store-manager/pull/43)), US-11 ([PR #46](https://github.com/Ybuuc/kaspi-store-manager/pull/46)) және US-13 ([PR #47](https://github.com/Ybuuc/kaspi-store-manager/pull/47)) `main` тармағына біріктірілді. [M2 issue #28](https://github.com/Ybuuc/kaspi-store-manager/issues/28) қабылдау шарттары тексеріліп жабылды; PR #48 финалдық нұсқасында 16 unit tests, Application tests және Security scans сәтті өтті. Жоспарлы 20.11.2026 күні нақты аяқталу күні ретінде қолданылмайды — нақты уақыт GitHub оқиғаларында сақталған.

Тапсырыстар тізімі әзірше тек оқу деректерін көрсетеді; нақты Kaspi интеграциясы [DEP-02 #23](https://github.com/Ybuuc/kaspi-store-manager/issues/23) бойынша ашық. Өндірістік DB hosting және жеке деректерді сақтау саясаты да бөлек шешіледі.

## M3 — Semester Release
- Қойма есебі дұрыс есептеледі.
- Admin/Employee құқықтары бөлінген.
- Must Have NFR талаптары бойынша тексеру нәтижелері тіркелген.
- Құжаттама және release notes жаңартылған.

## Future / Next Semester
Capacity шектеуіне байланысты US-12, US-14, US-16 және US-18 кейінгі кезеңге ауыстырылды (27 сағ).
