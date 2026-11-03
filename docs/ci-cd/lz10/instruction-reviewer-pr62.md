# Инструкция для независимого ревьюера — Kaspi Store Manager, PR №62

Дата подготовки: 03.11.2026. Автор PR: **Ybuuc**.
PR: https://github.com/Ybuuc/kaspi-store-manager/pull/62

Цель: проверить лабораторную №10 — CI-пайплайн, расчёт DORA по истории GitHub,
защиту main, релизный план и откат. После проверки нужно отправить review через
GitHub. Одно сообщение «всё хорошо» в обсуждении не считается Approve.

## 1. Что сначала делает владелец Ybuuc

1. Открыть https://github.com/Ybuuc/kaspi-store-manager/settings/access.
2. В разделе Collaborators нажать Add people.
3. Найти GitHub-аккаунт реального участника по его username и отправить приглашение.
4. Ревьюер принимает приглашение под **своим** аккаунтом.
   Для личного репозитория добавленный collaborator получает возможность записи;
   если интерфейс предлагает выбор роли, выбрать Write. Admin не требуется.
5. Открыть PR №62 → справа Reviewers → выбрать добавленного человека.

Автор не должен входить под аккаунтом ревьюера или одобрять собственную работу.

## 2. Что делает ревьюер: доступ и описание

1. Войти в GitHub под своим аккаунтом, принять приглашение в репозиторий.
2. Открыть PR №62 по ссылке выше.
3. Проверить: base — `main`, compare/head — `lz10-ci-dora`.
4. Прочитать описание PR и последние комментарии. Если автор добавил новые
   commits, проверять последнюю версию, а не старый скриншот запуска.
5. Открыть вкладку Files changed. Изучать изменения по файлам; проверенные файлы
   можно отмечать Viewed.

## 3. Предметный чек-лист

- [ ] `.github/workflows/ci.yml`: запуск на PR; jobs `lint`, `test`, `dependencies`,
  `build`; сборка зависит от предыдущих проверок; ошибки не скрыты через
  `continue-on-error`; ZIP публикуется только после успешной сборки и smoke.
- [ ] `requirements.lock`: фиксированы production-зависимости; Trivy проверяет
  их и блокирует HIGH/CRITICAL.
- [ ] `tools/build_release.py`: включает app.py, templates, static, requirements,
  README и manifest; исключает рабочую SQLite-базу и секреты.
- [ ] `tests/test_app.py`, `tests/test_dora.py`: проверки приложения и правил DORA.
- [ ] `tools/dora.py`: запросы GitHub API с пагинацией; CI и merge PR не выдаются
  за production-развёртывания; отсутствие данных не превращается в MTTR=0.
- [ ] `docs/ci-cd/lz10/data/github.json` и `reports/metrics.json`: реальная выгрузка
  и результат согласованы. Большой JSON не нужно построчно читать: проверить
  repository, start/end, requests и ключевые счётчики.
- [ ] `docs/ci-cd/lz10/dora-report.md`: явно указаны нулевая история production,
  N/A для недоступных метрик, ограничения и два улучшения с измеримыми целями.
- [ ] `release-plan.md`: окно, роли, Go/No-Go, backup SQLite, smoke и откат.
- [ ] `rollback-drill.md`: учебная тренировка отделена от настоящего incident;
  локальные измерения не названы production MTTR.

Важно для оценки: PR создаёт инструменты и честный базовый отчёт. Он **не доказывает**,
что все четыре DORA-метрики уже имеют численные production-значения.

## 4. Проверить GitHub Actions

Во вкладке Checks PR или внизу Conversation открыть результаты последнего commit.
Должны быть зелёными `lint`, `test`, `dependencies`, `build`.
Также посмотреть существующие Application tests и Security scans; если они красные,
прочитать причину и запросить исправление, а не игнорировать без объяснения.

Открыть запуск CI → build → проверить smoke распакованного пакета.
Внизу страницы запуска, раздел Artifacts: пакет `kaspi-store-manager-<SHA>`
и отчёт `dependency-scan`. Артефакт должен относиться к проверяемому запуску.
У запуска PR пакет может иметь временный merge SHA GitHub — это нормально для
проверки; выпускать нужно пакет успешного CI на main после merge.

## 5. Воспроизвести проверки на своём компьютере

Нужны Git и Python 3.12. Открыть PowerShell в папке, где ещё нет одноимённого clone:

```powershell
git clone --branch lz10-ci-dora https://github.com/Ybuuc/kaspi-store-manager.git
cd kaspi-store-manager
git rev-parse HEAD
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/dora.py report
```

Проверить код завершения после каждой команды. Ожидается Ruff без ошибок,
29 успешных тестов, ZIP в dist и dashboard.png в docs/ci-cd/lz10/reports.
Если автор добавит новые тесты, ориентироваться на актуальный состав и успешный итог.
Для отчёта используется сохранённая реальная выгрузка; GitHub-токен не нужен.

Для новой тренировки отката, при желании:

```powershell
.\.venv\Scripts\python.exe tools/rollback_drill.py --work-dir ../reviewer-drill-01 --report ../reviewer-drill-01.json
```

Папка reviewer-drill-01 должна ещё не существовать. Тренировка создаёт только
тестовые данные, поднимает HTTP на localhost и завершает свои процессы.

## 6. Отправить review

1. На вкладке Files changed нажать **Review changes**.
2. Если проверка пройдена, выбрать **Approve**.
3. Написать, что именно проверено. Пример, который нужно адаптировать к фактам:

   «Проверил workflow, публикацию ZIP, DORA-методику и план отката.
   Проверки последнего commit зелёные. Локально выполнены Ruff, тесты и сборка.
   Ограничение production-данных отражено явно. Замечаний к проверенной реализации нет».

   Не писать про локальную проверку, если её не выполняли.
4. Нажать **Submit review**.
5. На Conversation должна появиться запись, что ваш аккаунт approved these changes.

Если найдена ошибка: оставить комментарий на конкретной строке → Review changes →
**Request changes** → описать необходимое исправление → Submit review.
После исправлений проверить изменения и отправить новое Approve.

## 7. Слияние и типичные затруднения

После Approve владелец Ybuuc проверяет зелёные checks и отсутствие неразрешённых
обсуждений, затем нажимает доступную кнопку Merge pull request / Squash and merge
и подтверждает слияние. Самому ревьюеру сливать PR не обязательно.

- Не открывается приглашение: проверить аккаунт и попросить владельца повторить invite.
- Review не снимает блокировку: нужно Approve через Review changes, а не обычный comment;
  проверить принятие приглашения и права Write.
- Ветка устарела: автор нажимает Update branch и ждёт новый CI.
- После новых commits approve исчез: включён сброс устаревшего одобрения — проверить
  новые изменения и одобрить заново.
- Есть Request changes или открытые обсуждения: сначала исправить и разрешить их.
- Checks красные: открыть Details, передать автору конкретную ошибку.
- Нет кнопки Merge: смотреть сообщение блокировки; не отключать защиту main.

Официальные шаги review:
https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request
