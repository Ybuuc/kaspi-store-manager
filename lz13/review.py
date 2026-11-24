"""Interactive human timing, run both steps with the same scope."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["manual", "review"])
    parser.add_argument("folder", type=Path, help="private folder for one trial")
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    target = args.folder / (args.step + ".json")
    if target.exists():
        parser.error("Этот замер сохранён. Для нового испытания укажите новую папку.")
    if args.step == "review" and not (args.folder / "manual.json").exists():
        parser.error("Сначала выполните независимое ручное составление.")
    print("Оба спринта. Укажите число слияний в main, 1–2 темы и PR для каждого.")
    if args.step == "manual":
        print("Закройте готовый отчёт. Используйте GitHub PR и границы из README.")
    else:
        print("Откройте исходный отчёт модели. Сверьте каждое утверждение и исправьте ошибки.")
    input("Подготовьте рабочее место. Enter запускает таймер. ")
    start = perf_counter()
    input("Выполните работу в редакторе, сохраните результат. Enter завершает замер. ")
    seconds = round(perf_counter() - start, 2)
    record = {"seconds": seconds, "utc": datetime.now(timezone.utc).isoformat(),
              "notes": input("Имя файла с вашим текстом или журналом исправлений: ")}
    if args.step == "review":
        manual = json.loads((args.folder / "manual.json").read_text(encoding="utf-8"))["seconds"]
        setup = float(input("Другие активные затраты PM на этот выпуск, секунд (0 если нет): "))
        if setup < 0:
            parser.error("Затраты должны быть неотрицательными.")
        record.update(other_active_seconds=setup, net_seconds=round(manual - seconds - setup, 2))
    with target.open("x", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)
    print(record)


if __name__ == "__main__":
    main()
