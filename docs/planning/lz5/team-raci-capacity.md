# ЛЗ 5 — Team Topology, RACI және Capacity

## Team Topology
Жоба үшін **stream-aligned small team** топологиясы таңдалды. Оқу жобасы шағын болғандықтан, бір студент бірнеше рөлді логикалық түрде біріктіреді.

## Рөлдер
- Project Manager — жоспарлау, backlog, releases.
- Business Analyst — User Stories, Acceptance Criteria.
- Developer — функционалды іске асыру.
- Tester — functional және NFR verification.
- DevOps / Repository Owner — GitHub, branches, release artifacts.
- Store Owner / Customer — талаптарды және бизнес шешімдерді келісу.

## RACI

| Артефакт | PM | BA | Developer | Tester | Store Owner |
|---|---|---|---|---|---|
| WBS | A/R | C | I | I | I |
| Product Backlog | A | R | C | C | C |
| Release Plan | A/R | C | C | C | I |
| Architecture | I | C | A/R | C | I |
| User Stories | A | R | C | C | C |
| Source Code | I | I | A/R | C | I |
| Test Results | I | C | C | A/R | I |
| NFR | A | R | C | R | C |
| Release | A | I | R | R | I |

R — Responsible, A — Accountable, C — Consulted, I — Informed.

## Capacity

Семестр: 15 оқу аптасы.  
Жобаға бөлінетін уақыт: 7 сағ/апта.

Номиналды capacity: **15 × 7 = 105 сағ**.

Түзетулер:
- басқа пәндер мен СӨЖ: 15%;
- аралық бақылау/емтихан: 10%;
- күтпеген жұмыстар және резерв: 10%.

Қолжетімді коэффициент: **65%**.

Нақты capacity: **105 × 0.65 = 68.25 ≈ 68 сағ**.

Қазіргі US-01–US-18 backlog бағасы: **92 сағ**, сондықтан толық backlog семестр capacity-інен **24 сағатқа артық**.

Capacity-adjusted semester scope:
- Increment 1: 33 сағ
- Increment 2: 20 сағ
- Increment 3: 12 сағ
- Reserve: 3 сағ
- Барлығы: 68 сағ

Future scope: US-12, US-14, US-16, US-18 = 27 сағ.
