# ЛЗ 9 — Екі спринтті симуляциялау

| Артефакт | Файл |
|---|---|
| Тақта, WIP, саясаттар, DoR, DoD | [01-workflow.md](01-workflow.md) |
| Спринтті жоспарлау (мақсат, capacity, Ready тізімі) | [02-sprint-planning.md](02-sprint-planning.md) |
| Ретроспектива хаттамасы (бір әрекет + жауапты) | [03-retro-protocol.md](03-retro-protocol.md) |
| Метрикалар есебі (генерацияланады) | `reports/flow-metrics-report.md` |
| Метрика скрипті | [scripts/flow_metrics.py](scripts/flow_metrics.py) |
| Issue / PR шаблондары (DoR / DoD) | `.github/ISSUE_TEMPLATE/user-story.md`, `.github/pull_request_template.md` |

## Орындау реті

**01.11.2026 күйі:** M1-дің US-01–US-08 функционалы `main` ішінде және Project #1-де `Done`. [Sprint жоспарының нақты прогресс ескертпесі](02-sprint-planning.md#нақты-прогресс--01112026) бастапқы жоспар мен орындалу айырмасын түсіндіреді. Sprint 1/2 метрикалары мен ретроспектива болашақ күндерге алдын ала толтырылмайды.

1. **Тақтаны баптау.** GitHub Project #1 → Status өрісінің опциялары: `Backlog`, `Ready`, `In Progress`, `Review`, `Done`
   (дәл осындай атаулармен). Шаблондарды `main`-ге қосыңыз. Лимиттер — саясат бойынша ([01-workflow.md](01-workflow.md)).
2. **Спринт 1 жоспарлау** (2026-10-27): [02-sprint-planning.md](02-sprint-planning.md) толтырыңыз, элементтерді Ready-ге өткізіңіз.
3. **Апта бойы жұмыс:** элементтерді тақтада нақты күйі бойынша ғана жылжытыңыз (метрика сол оқиғалардан есептеледі).
4. **Спринт 1 қарау + ретро** (2026-11-01/02): 
   ```
   gh auth refresh -s read:project
   python3 docs/process/lz9/scripts/flow_metrics.py export
   python3 docs/process/lz9/scripts/flow_metrics.py report
   ```
   Есептің Спринт 1 бөлімі мен CFD бойынша [03-retro-protocol.md](03-retro-protocol.md) толтырыңыз — **дәл бір** әрекет.
5. **Спринт 2** (2026-11-03 … 11-09): әрекетті жоспарға қосып орындаңыз.
6. **Қорытынды:** `export` + `report` қайта іске қосыңыз, `reports/flow-metrics-report.md` 5-бөлімін толтырыңыз,
   ретро хаттамасының соңғы кестесін (әрекетті тексеру) толтырыңыз.

Күндер басқа болса: `--s1 YYYY-MM-DD:YYYY-MM-DD --s2 YYYY-MM-DD:YYYY-MM-DD`.

## Кепілдіктер мен шектеулер

- Барлық сан тақтаның Status өзгеру тарихынан (issue timeline `ProjectV2ItemStatusChangedEvent`) есептеледі;
  шикі экспорт `data/raw-export.json` ретінде сақталады, есепте оның SHA-256 көрсетіледі.
- `selftest` командасы есептеу формулаларын синтетикалық деректермен тексереді; ол дерек есепке кірмейді.
- Тақтада тек Status өрісі арқылы жылжытылған элементтер есептеледі. Элемент In Progress-ке түспей Done болса,
  оның cycle time есептелмейді және есепте ескерту шығады.
- Спринтте 2–5 элемент болса, p85 мен орташа тек бағыт көрсетеді; мұны есептің 5-бөлімінде жазыңыз.
