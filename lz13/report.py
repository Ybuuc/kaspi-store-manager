"""PM merge digest. Python 3.10+, standard library only."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = "Ybuuc/kaspi-store-manager"
MODEL = "qwen2.5:1.5b"
UTC = timezone.utc
LOCAL = timezone(timedelta(hours=5))
TOPICS = {
    33: "Kanban и метрики", 40: "вход и каталог", 41: "каталог, склад и доступ",
    42: "статус M1", 43: "архив товаров", 44: "инструкция установки",
    45: "повторный запуск", 46: "цены и аудит", 47: "фильтр учебных заказов",
    48: "предупреждения об остатках", 49: "статус M2", 50: "отчёт склада и роли",
    51: "история цен, продажи и аудит", 52: "переходы статусов заказов",
    53: "резервное копирование", 54: "локальная демонстрация", 58: "перепланирование спринта",
    59: "метрики задач", 60: "календарь и ретроспектива", 61: "итоги спринтов",
    62: "CI и DORA", 74: "интеграция kaspibot",
}


def moment(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def windows(mode):
    if mode == "sprints":
        dates = list(map(moment, ["2026-10-27T00:00:00+05:00",
                                  "2026-11-01T15:07:30+05:00", "2026-11-01T15:24:00+05:00"]))
        return [("Спринт 1", dates[0], dates[1]), ("Спринт 2", dates[1], dates[2])]
    today = datetime.now(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today - timedelta(days=today.weekday())
    return [("Неделя", end - timedelta(days=7), end)]


def fetch(start):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ksm-pm-assistant"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    rows = {}
    page = 1
    while True:
        url = (f"https://api.github.com/repos/{REPO}/pulls?base=main&state=closed"
               f"&sort=updated&direction=desc&per_page=100&page={page}")
        with urlopen(Request(url, headers=headers), timeout=60) as response:
            data = json.load(response)
        for pr in data:
            if pr["merged_at"]:
                rows[pr["number"]] = {"number": pr["number"], "merged_at": pr["merged_at"]}
        if len(data) < 100 or moment(data[-1]["updated_at"]) < start:
            return list(rows.values())
        page += 1


def summary(facts):
    prompt = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    body = {"model": MODEL, "prompt": prompt.replace("{facts}", json.dumps(facts, ensure_ascii=False)),
            "stream": False, "format": "json", "options": {"temperature": 0, "seed": 13}}
    request = Request("http://127.0.0.1:11434/api/generate", json.dumps(body).encode(),
                      {"Content-Type": "application/json"})
    with urlopen(request, timeout=300) as response:
        raw = json.load(response)
    return raw


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["sprints", "week"], default="week")
    p.add_argument("--snapshot", type=Path)
    p.add_argument("--facts-only", action="store_true")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    started = perf_counter()
    args.out.mkdir(parents=True, exist_ok=False)
    periods = windows(args.mode)
    rows = (json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot
            else fetch(periods[0][1]))
    write(args.out / "source.json", rows)
    report = ["# Kaspi Store Manager · сводка слияний в main", "",
              "Время границ UTC+5. Начало включено, конец исключён.", ""]
    for index, (name, start, end) in enumerate(periods, 1):
        prs = sorted((r for r in rows if start <= moment(r["merged_at"]) < end),
                     key=lambda r: (r["merged_at"], r["number"]))
        facts = {"period": name, "count": len(prs), "prs": [
            {"number": r["number"], "topic": TOPICS.get(r["number"], "тема не размечена")} for r in prs]}
        write(args.out / f"facts-{index}.json", facts)
        report += [f"## {name}", "", f"{start.isoformat()} … {end.isoformat()}", "",
                   f"Влито PR: **{len(prs)}**.", ""]
        if not args.facts_only:
            raw = summary(facts)
            write(args.out / f"ollama-{index}.json", raw)
            paragraph = json.loads(raw["response"])["summary"]
            if not isinstance(paragraph, str) or not paragraph.strip():
                raise ValueError("Ollama returned an empty or invalid summary")
            report += [paragraph, ""]
        for row, item in zip(prs, facts["prs"]):
            n = item["number"]
            report.append(f"- [#{n}](https://github.com/{REPO}/pull/{n}) · "
                          f"{row['merged_at']} · {item['topic']}")
        report.append("")
    (args.out / "report.md").write_text("\n".join(report), encoding="utf-8")
    write(args.out / "run.json", {"model": None if args.facts_only else MODEL,
                                  "elapsed_seconds": round(perf_counter() - started, 3)})
    print("\n".join(report))


if __name__ == "__main__":
    main()
