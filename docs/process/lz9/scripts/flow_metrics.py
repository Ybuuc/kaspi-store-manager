#!/usr/bin/env python3
"""
ЛЗ 9 — ағын метрикаларын GitHub Projects тақтасынан экспорттау және есептеу.

Дерек көзі: GitHub Projects (v2) тақтасындағы Status өрісінің өзгеру тарихы
(issue timeline: ProjectV2ItemStatusChangedEvent). Ешқандай мән қолмен енгізілмейді.

Қолдану:
  gh auth refresh -s read:project          # бір рет, тақтаны оқуға рұқсат
  python3 flow_metrics.py export           # тақтаны data/raw-export.json файлына жүктеу
  python3 flow_metrics.py report           # метрикалар, CFD және есепті жасау
  python3 flow_metrics.py selftest         # есептеу логикасын синтетикалық деректермен тексеру

Тек стандартты кітапхана керек; CFD суреті үшін matplotlib (болмаса сурет өткізіліп кетеді).
"""
import argparse
import csv
import hashlib
import json
import re
import math
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLUMNS = ["Backlog", "Ready", "In Progress", "Review", "Done"]
DEFAULT_LIMITS = {"Ready": 4, "In Progress": 2, "Review": 2}
DEFAULT_S1 = "2026-10-27:2026-11-02"
DEFAULT_S2 = "2026-11-03:2026-11-09"

QUERY = """
query($login:String!, $number:Int!, $after:String) {
  user(login:$login) {
    projectV2(number:$number) {
      title
      items(first:50, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          status: fieldValueByName(name:"Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number title url createdAt closedAt
              timelineItems(first:100, itemTypes:[ADDED_TO_PROJECT_V2_EVENT, PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) {
                pageInfo { hasNextPage }
                nodes {
                  __typename
                  ... on AddedToProjectV2Event { createdAt }
                  ... on ProjectV2ItemStatusChangedEvent { createdAt previousStatus status }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------- helpers
def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def norm_status(s):
    """Бағанның канондық атауы. Бос / 'No Status' = Backlog."""
    if s is None or str(s).strip() == "" or str(s).strip().lower() == "no status":
        return "Backlog"
    for c in COLUMNS:
        if c.lower() == str(s).strip().lower():
            return c
    raise ValueError(
        f"Белгісіз баған '{s}'. Тақтадағы Status опциялары мынадай болуы тиіс: {', '.join(COLUMNS)}"
    )


def percentile_nearest_rank(values, p):
    if not values:
        return None
    v = sorted(values)
    k = max(1, math.ceil(p / 100 * len(v)))
    return v[k - 1]


def mean(values):
    return sum(values) / len(values) if values else None


def median(values):
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def fmt(x, nd=1):
    return "—" if x is None else f"{x:.{nd}f}"


def parse_limits(s):
    if not s:
        return dict(DEFAULT_LIMITS)
    out = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[norm_status(k)] = int(v)
    return out


def parse_window(spec, tz):
    if ".." in spec:
        a, b = spec.split("..", 1)
        start, end = datetime.fromisoformat(a), datetime.fromisoformat(b)
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start
        end = end.replace(tzinfo=tz) if end.tzinfo is None else end
    else:
        a, b = spec.split(":", 1)
        start = datetime.fromisoformat(a).replace(tzinfo=tz)
        end = datetime.fromisoformat(b).replace(tzinfo=tz) + timedelta(days=1)
    if end <= start:
        raise ValueError(f"Спринт аралығы жарамсыз: {spec}")
    return start, end


# --------------------------------------------------------------------------- export
def gh_graphql(**variables):
    cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}"]
    for k, v in variables.items():
        if v is None:
            continue
        cmd += (["-F", f"{k}={v}"] if isinstance(v, int) else ["-f", f"{k}={v}"])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(
            "gh api graphql қатесі. 'gh auth status' тексеріңіз және тақтаны оқуға "
            "рұқсат беріңіз: gh auth refresh -s read:project\n" + r.stderr.strip()
        )
    return json.loads(r.stdout)


def cmd_export(args):
    items, after, title = [], None, None
    while True:
        res = gh_graphql(login=args.owner, number=args.project, after=after)
        if res.get("errors"):
            sys.exit("GraphQL errors: " + json.dumps(res["errors"], ensure_ascii=False))
        proj = res["data"]["user"]["projectV2"]
        title = proj["title"]
        page = proj["items"]
        items += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    for n in items:
        c = n.get("content") or {}
        if c.get("timelineItems", {}).get("pageInfo", {}).get("hasNextPage"):
            print(f"ЕСКЕРТУ: #{c.get('number')} үшін 100-ден артық оқиға, тарих толық емес.", file=sys.stderr)
    out = Path(args.data) / "raw-export.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"GitHub Projects v2, user={args.owner}, project={args.project}",
        "project_title": title,
        "items": items,
    }
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Экспортталды: {len(items)} элемент → {out}")


# --------------------------------------------------------------------------- model
def build_items(raw):
    """Сырой экспорттан [{number,title,events:[(ts,status)],...}] жасайды."""
    items, skipped = [], 0
    for n in raw["items"]:
        c = n.get("content") or {}
        if "number" not in c:  # draft item / PR — timeline жоқ
            skipped += 1
            continue
        tl = c["timelineItems"]["nodes"]
        added = [parse_ts(e["createdAt"]) for e in tl if e["__typename"] == "AddedToProjectV2Event"]
        changes = sorted(
            [e for e in tl if e["__typename"] == "ProjectV2ItemStatusChangedEvent"],
            key=lambda e: e["createdAt"],
        )
        added_at = min(added) if added else parse_ts(c["createdAt"])
        current = norm_status((n.get("status") or {}).get("name"))
        initial = norm_status(changes[0]["previousStatus"]) if changes else current
        events = [(added_at, initial)]
        for e in changes:
            events.append((parse_ts(e["createdAt"]), norm_status(e["status"])))
        events.sort(key=lambda x: x[0])
        items.append({"number": c["number"], "title": c["title"], "events": events, "current": current})
    return items, skipped


def is_task_issue(item):
    """Only actionable task cards belong in LZ9 flow metrics.

    Parent stories, dependencies and milestones may be displayed on the board,
    but the workflow explicitly excludes them from WIP and throughput.
    """
    title = item["title"].strip()
    return bool(
        title.startswith(("[TASK]", "[DOCS]", "SEC-", "PRIV-", "S2-"))
        or re.match(r"^US-\d+\.\d+\b", title)
    )


def status_at(item, t):
    s = None
    for ts, st in item["events"]:
        if ts <= t:
            s = st
        else:
            break
    return s


def status_index_at(item, t):
    current = status_at(item, t)
    return COLUMNS.index(current) if current is not None else None


def started_at(item):
    for ts, st in item["events"]:
        if st == "In Progress":
            return ts
    return None


def done_at(item):
    if item["events"][-1][1] != "Done":
        return None
    return item["events"][-1][0]


def dwell(item, now):
    """Әр баған бойынша өткізілген уақыт (күн)."""
    d = {c: 0.0 for c in COLUMNS}
    ev = item["events"]
    for i, (ts, st) in enumerate(ev):
        nxt = ev[i + 1][0] if i + 1 < len(ev) else now
        d[st] += (nxt - ts).total_seconds() / 86400
    return d


def peak_wip(items, start, end, column):
    """Highest concurrent card count, including transitions within a day."""
    if end <= start:
        return 0
    instants = {start}
    for item in items:
        instants.update(ts for ts, _ in item["events"] if start <= ts < end)
    return max((sum(status_at(item, t) == column for item in items) for t in sorted(instants)), default=0)


# --------------------------------------------------------------------------- metrics
def sprint_metrics(items, start, end, now, limits):
    horizon = min(end, now)
    done_items, cts, warnings = [], [], []
    for it in items:
        d = done_at(it)
        if d is not None and start <= d < end:
            s = started_at(it)
            if s is None:
                warnings.append(f"#{it['number']}: In Progress арқылы өтпеген, cycle time есептелмейді")
                ct = None
            else:
                ct = (d - s).total_seconds() / 86400
                cts.append(ct)
            done_items.append({"number": it["number"], "title": it["title"], "started": s, "done": d, "ct": ct})

    # CFD: күн соңындағы немесе қысқа интервалда әр оқиғадан кейінгі түсірілім.
    days, cum, bands = [], [], []
    if end - start < timedelta(days=1):
        snapshots = sorted({start, horizon} | {
            ts for it in items for ts, _ in it["events"] if start < ts < horizon
        }) if horizon > start else []
    else:
        snapshots = []
        day = start
        while day < horizon:
            snapshots.append(min(day + timedelta(days=1), horizon))
            day += timedelta(days=1)
    for snap in snapshots:
        counts = [0] * len(COLUMNS)
        for it in items:
            m = status_index_at(it, snap)
            if m is not None:
                for k in range(m + 1):
                    counts[k] += 1
        band = [counts[k] - (counts[k + 1] if k + 1 < len(COLUMNS) else 0) for k in range(len(COLUMNS))]
        days.append(snap.astimezone(start.tzinfo).isoformat(timespec="seconds") if end - start < timedelta(days=1) else (snap - timedelta(microseconds=1)).astimezone(start.tzinfo).date().isoformat())
        cum.append(counts)
        bands.append(band)

    avg_wip = {c: mean([b[COLUMNS.index(c)] for b in bands]) for c in COLUMNS} if bands else {c: None for c in COLUMNS}

    # Done болған элементтер бойынша баған ішіндегі орташа уақыт
    avg_dwell = {}
    for c in ("Ready", "In Progress", "Review"):
        vals = [dwell(it, now)[c] for it in items if done_at(it) and start <= done_at(it) < end]
        avg_dwell[c] = mean(vals)

    # Күн соңындағы WIP нөл болса, внутридневной пик оқиға уақытынан алынады.
    peak_by_column = {c: peak_wip(items, start, horizon, c) for c in ("Ready", "In Progress", "Review")}
    use_peak = all((avg_wip[c] or 0) == 0 for c in ("Ready", "In Progress", "Review"))
    cand, best = None, -1
    for c in ("Ready", "In Progress", "Review"):
        if avg_wip[c] is None or c not in limits:
            continue
        ratio = (peak_by_column[c] if use_peak else avg_wip[c]) / limits[c]
        key = (ratio, avg_dwell[c] or 0)
        if cand is None or key > best:
            cand, best = c, key

    complete = end <= now
    if not complete:
        warnings.append("Спринт әлі аяқталмаған — метрикалар жартылай.")
    return {
        "start": start.date().isoformat(),
        "end": (end - timedelta(days=1)).date().isoformat() if end.time() == datetime.min.time() else end.date().isoformat(),
        "period": f"{start.isoformat(timespec='seconds')} … {end.isoformat(timespec='seconds')} (соңы есепке кірмейді)",
        "complete": complete,
        "throughput": len(done_items),
        "ct_n": len(cts),
        "ct_mean": mean(cts),
        "ct_median": median(cts),
        "ct_p85": percentile_nearest_rank(cts, 85),
        "done_items": done_items,
        "cfd_days": days,
        "cfd_cum": cum,
        "cfd_bands": bands,
        "avg_wip": avg_wip,
        "peak_wip": peak_by_column,
        "avg_dwell": avg_dwell,
        "bottleneck": cand,
        "bottleneck_ratio": best[0] if cand else None,
        "bottleneck_basis": "peak WIP/лимит" if use_peak else "орташа WIP снимков CFD/лимит",
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- output
def plot_cfd(m, path, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib жоқ — CFD суреті өткізілді (pip install matplotlib).", file=sys.stderr)
        return False
    if not m["cfd_bands"]:
        return False
    order = list(reversed(COLUMNS))  # төменде Done
    series = [[b[COLUMNS.index(c)] for b in m["cfd_bands"]] for c in order]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.stackplot(range(len(m["cfd_days"])), series, labels=order)
    count = len(m["cfd_days"])
    ticks = list(range(0, count, max(1, math.ceil(count / 7))))
    if ticks[-1] != count - 1:
        ticks.append(count - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels([m["cfd_days"][i][11:19] if "T" in m["cfd_days"][i] else m["cfd_days"][i][5:]
                        for i in ticks], rotation=45, ha="right")
    ax.set_ylabel("Элемент саны (кумулятивті)")
    ax.set_title(title)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def write_outputs(items, m1, m2, limits, out_dir, raw_path, raw_meta):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["issue", "title", "sprint", "started_in_progress", "done", "cycle_time_days"])
        for label, m in (("S1", m1), ("S2", m2)):
            for d in m["done_items"]:
                w.writerow([d["number"], d["title"], label,
                            d["started"].isoformat() if d["started"] else "",
                            d["done"].isoformat(), fmt(d["ct"], 4) if d["ct"] is not None else ""])
    for label, m in (("1", m1), ("2", m2)):
        with open(out_dir / f"cfd-sprint{label}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date"] + COLUMNS + [f"band_{c}" for c in COLUMNS])
            for d, cu, b in zip(m["cfd_days"], m["cfd_cum"], m["cfd_bands"]):
                w.writerow([d] + cu + b)
    png1 = plot_cfd(m1, out_dir / "cfd-sprint1.png", f"CFD — Спринт 1 ({m1['start']} … {m1['end']})")
    png2 = plot_cfd(m2, out_dir / "cfd-sprint2.png", f"CFD — Спринт 2 ({m2['start']} … {m2['end']})")

    def delta(a, b, nd=1):
        if a is None or b is None:
            return "—"
        return f"{b - a:+.{nd}f}"

    sha = hashlib.sha256(Path(raw_path).read_bytes()).hexdigest()
    L = []
    L.append("# ЛЗ 9 — Екі спринттің ағын метрикалары\n")
    L.append("> Бұл файлдағы барлық сандар `scripts/flow_metrics.py` арқылы GitHub Projects тақтасының "
             "оқиғалар тарихынан есептелген. Қолмен енгізілген мән жоқ.\n")
    L.append("## 1. Дерек көзі\n")
    L.append(f"- Көз: {raw_meta.get('source')} (тақта: «{raw_meta.get('project_title')}»)")
    L.append(f"- Экспорт уақыты: {raw_meta.get('exported_at')}")
    L.append(f"- Шикі экспорт: `data/raw-export.json`, SHA-256: `{sha}`")
    L.append(f"- Метрикаға алынған task-issue: {len(items)}; тақтадағы барлық issue: {len(raw_meta['items'])}")
    L.append("- Parent US, dependency және milestone карточкалары throughput, cycle time, CFD және WIP есебінен шығарылды; олар task-issue емес.")
    L.append(f"- Спринт 1: {m1['period']}; Спринт 2: {m2['period']}")
    L.append(f"- WIP-лимиттер: " + ", ".join(f"{k} ≤ {v}" for k, v in limits.items()))
    L.append("- Анықтамалар: cycle time = In Progress бағанына алғаш кіруден Done-ға дейінгі күнтізбелік күн; "
             "throughput = спринт ішінде Done болған элемент саны; p85 = nearest-rank әдісі.\n")

    L.append("## 2. Салыстыру кестесі\n")
    L.append("| Метрика | Спринт 1 | Спринт 2 | Δ (С2 − С1) |")
    L.append("|---|---:|---:|---:|")
    L.append(f"| Throughput (Done саны) | {m1['throughput']} | {m2['throughput']} | {m2['throughput'] - m1['throughput']:+d} |")
    L.append(f"| Cycle time, орташа (күн) | {fmt(m1['ct_mean'], 4)} | {fmt(m2['ct_mean'], 4)} | {delta(m1['ct_mean'], m2['ct_mean'], 4)} |")
    L.append(f"| Cycle time, медиана (күн) | {fmt(m1['ct_median'], 4)} | {fmt(m2['ct_median'], 4)} | {delta(m1['ct_median'], m2['ct_median'], 4)} |")
    L.append(f"| Cycle time, p85 (күн) | {fmt(m1['ct_p85'], 4)} | {fmt(m2['ct_p85'], 4)} | {delta(m1['ct_p85'], m2['ct_p85'], 4)} |")
    L.append(f"| Cycle time үшін элемент саны (n) | {m1['ct_n']} | {m2['ct_n']} | |")
    for c in ("Ready", "In Progress", "Review"):
        L.append(f"| Орташа WIP снимков CFD: {c} (лимит {limits.get(c, '—')}) | {fmt(m1['avg_wip'][c], 2)} | {fmt(m2['avg_wip'][c], 2)} | {delta(m1['avg_wip'][c], m2['avg_wip'][c], 2)} |")
    for c in ("Ready", "In Progress", "Review"):
        L.append(f"| Ең жоғары бірмезгілдегі WIP: {c} | {m1['peak_wip'][c]} | {m2['peak_wip'][c]} | {m2['peak_wip'][c] - m1['peak_wip'][c]:+d} |")
    for c in ("Ready", "In Progress", "Review"):
        L.append(f"| Баған ішіндегі орташа уақыт: {c} (күн) | {fmt(m1['avg_dwell'][c])} | {fmt(m2['avg_dwell'][c])} | {delta(m1['avg_dwell'][c], m2['avg_dwell'][c])} |")
    L.append(f"| Тар орынға үміткер (WIP/лимит ең жоғары) | {m1['bottleneck'] or '—'} | {m2['bottleneck'] or '—'} | |")
    L.append("")

    for label, m, png in (("1", m1, png1), ("2", m2, png2)):
        L.append(f"## {2 + int(label)}. Спринт {label} — толығырақ\n")
        L.append(f"Кезең: {m['period']}. Аяқталған элементтер:\n")
        if m["done_items"]:
            L.append("| Issue | Атауы | Cycle time (күн) |")
            L.append("|---|---|---:|")
            for d in m["done_items"]:
                L.append(f"| #{d['number']} | {d['title']} | {fmt(d['ct'], 4)} |")
        else:
            L.append("_Бұл спринтте Done болған элемент жоқ._")
        L.append("")
        if png:
            L.append(f"![CFD, спринт {label}](cfd-sprint{label}.png)\n")
        L.append(f"CFD деректері: `cfd-sprint{label}.csv`.")
        if m["bottleneck"]:
            L.append(f"\nТар орынға үміткер: **{m['bottleneck']}** "
                     f"({m['bottleneck_basis']} = {m['bottleneck_ratio']:.2f}). Бұл — формулалық үміткер; "
                     "түпкілікті қорытындыны CFD жолақтарын қарап ретроспективада растаңыз.")
        for w in m["warnings"]:
            L.append(f"\n⚠ {w}")
        L.append("")

    L.append("## 5. Ағында не өзгерді және неге\n")
    L.append("<!-- ТОЛТЫРУ (қолмен, жоғарыдағы сандарға сүйеніп): -->")
    L.append("- Ретроспективада қабылданған жалғыз әрекет (03-retro-protocol.md): …")
    L.append("- Оның метрикасы мен мақсаты, нақты нәтиже (кестедегі Δ): …")
    L.append("- Гипотеза расталды ма? Неге (CFD жолақтары, баған ішіндегі уақыт бойынша): …")
    L.append("- Басқа факторлар (спринт көлемі, өзгерген талаптар, сыртқы жұмыс): …")
    L.append("- Шектеулер: n өте аз болса, p85 мен орташа статистикалық мағынасыз; тек трендті көрсетеді.\n")
    (out_dir / "flow-metrics-report.md").write_text("\n".join(L), encoding="utf-8")
    return out_dir / "flow-metrics-report.md"


# --------------------------------------------------------------------------- commands
def run_report(raw, args, now=None):
    tz = timezone(timedelta(hours=args.tz_offset))
    now = now or datetime.now(timezone.utc)
    limits = parse_limits(args.limits)
    all_items, skipped = build_items(raw)
    items = [item for item in all_items if is_task_issue(item)]
    if skipped:
        print(f"Ескерту: {skipped} элемент (draft/PR) өткізілді — оларда timeline жоқ.", file=sys.stderr)
    s1 = parse_window(args.s1, tz)
    s2 = parse_window(args.s2, tz)
    m1 = sprint_metrics(items, *s1, now, limits)
    m2 = sprint_metrics(items, *s2, now, limits)
    return items, m1, m2, limits


def cmd_report(args):
    raw_path = Path(args.data) / "raw-export.json"
    if not raw_path.exists():
        sys.exit(f"{raw_path} жоқ. Алдымен: python3 flow_metrics.py export")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    items, m1, m2, limits = run_report(raw, args)
    rp = write_outputs(items, m1, m2, limits, args.out, raw_path, raw)
    print(f"Есеп жасалды: {rp}")
    for lbl, m in (("S1", m1), ("S2", m2)):
        print(f"{lbl}: throughput={m['throughput']} mean={fmt(m['ct_mean'])} p85={fmt(m['ct_p85'])} bottleneck={m['bottleneck']}")


def cmd_selftest(args):
    """Есептеу логикасын тексеру. СИНТЕТИКАЛЫҚ дерек — есепке кірмейді."""
    def add(t): return {"__typename": "AddedToProjectV2Event", "createdAt": t}
    def chg(t, a, b): return {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": t, "previousStatus": a, "status": b}
    def item(n, cur, evs, t0="2026-10-26T05:00:00Z"):
        return {"status": {"name": cur},
                "content": {"number": n, "title": f"[TASK] T{n}", "url": "", "createdAt": t0, "closedAt": None,
                            "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [add(t0)] + evs}}}
    raw = {"exported_at": "selftest", "source": "SYNTHETIC", "project_title": "selftest", "items": [
        item(1, "Done", [chg("2026-10-27T05:00:00Z", "Backlog", "Ready"), chg("2026-10-27T09:00:00Z", "Ready", "In Progress"),
                         chg("2026-10-28T09:00:00Z", "In Progress", "Review"), chg("2026-10-29T09:00:00Z", "Review", "Done")]),
        item(2, "Done", [chg("2026-10-27T05:00:00Z", "Backlog", "Ready"), chg("2026-10-28T09:00:00Z", "Ready", "In Progress"),
                         chg("2026-10-30T09:00:00Z", "In Progress", "Review"), chg("2026-11-02T09:00:00Z", "Review", "Done")]),
        item(3, "Done", [chg("2026-10-27T05:00:00Z", "Backlog", "Ready"), chg("2026-10-29T09:00:00Z", "Ready", "In Progress"),
                         chg("2026-11-01T09:00:00Z", "In Progress", "Review"), chg("2026-11-04T09:00:00Z", "Review", "Done")]),
        item(4, "Done", [chg("2026-11-03T09:00:00Z", "Backlog", "Ready"), chg("2026-11-04T09:00:00Z", "Ready", "In Progress"),
                         chg("2026-11-05T09:00:00Z", "In Progress", "Done")]),
        item(5, "Backlog", []),
    ]}
    ns = argparse.Namespace(tz_offset=5, limits=None, s1=DEFAULT_S1, s2=DEFAULT_S2)
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    items, m1, m2, limits = run_report(raw, ns, now)
    assert is_task_issue({"title": "US-02.1 — Display product list fields"})
    assert not is_task_issue({"title": "US-02 — Тауарлар тізімін көру"})
    assert not is_task_issue({"title": "M3 — Semester Release"})
    reopened = {"events": [(parse_ts("2026-10-27T00:00:00Z"), "Done"),
                            (parse_ts("2026-10-28T00:00:00Z"), "Backlog")]}
    assert status_index_at(reopened, parse_ts("2026-10-29T00:00:00Z")) == 0
    assert m1["peak_wip"]["In Progress"] >= 1
    short_start, short_end = parse_window("2026-10-27T00:00+05:00..2026-11-01T15:00+05:00", timezone(timedelta(hours=5)))
    assert short_start.isoformat() == "2026-10-27T00:00:00+05:00"
    assert short_end.isoformat() == "2026-11-01T15:00:00+05:00"
    approx = lambda a, b: abs(a - b) < 1e-9
    assert m1["throughput"] == 2 and m2["throughput"] == 2, (m1["throughput"], m2["throughput"])
    assert approx(m1["ct_mean"], 3.5) and approx(m1["ct_p85"], 5.0) and approx(m1["ct_median"], 3.5)
    assert approx(m2["ct_mean"], 3.5) and approx(m2["ct_p85"], 6.0) and approx(m2["ct_median"], 3.5)
    assert len(m1["cfd_days"]) == 7 and len(m2["cfd_days"]) == 7
    # 27.10 соңы: 5 элемент тақтада, 3-еуі Ready-ден әрі өтті (Ready-ге 05:00Z=10:00 жергілікті)
    assert m1["cfd_cum"][0][0] == 5, m1["cfd_cum"][0]
    assert m1["cfd_cum"][-1][4] == 2, m1["cfd_cum"][-1]       # 02.11 соңында Done = 2
    assert m2["cfd_cum"][-1][4] == 4, m2["cfd_cum"][-1]       # 09.11 соңында Done = 4
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "raw.json"
        rp.write_text(json.dumps(raw))
        out = write_outputs(items, m1, m2, limits, td, rp, raw)
        assert out.exists() and "Throughput" in out.read_text(encoding="utf-8")
    print("SELFTEST OK (синтетикалық дерек; нақты есепке қолданылмайды)")


def main():
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export", "report", "selftest"])
    ap.add_argument("--owner", default="Ybuuc", help="GitHub user — тақта иесі")
    ap.add_argument("--project", type=int, default=1, help="GitHub Project нөмірі (…/projects/1)")
    ap.add_argument("--data", default=str(here / "data"))
    ap.add_argument("--out", default=str(here / "reports"))
    ap.add_argument("--s1", default=DEFAULT_S1, help="Спринт 1: YYYY-MM-DD:YYYY-MM-DD немесе ISO-басы..ISO-соңы")
    ap.add_argument("--s2", default=DEFAULT_S2, help="Спринт 2: YYYY-MM-DD:YYYY-MM-DD немесе ISO-басы..ISO-соңы")
    ap.add_argument("--tz-offset", type=int, default=5, help="Сағат белдеуі, UTC+N (Қазақстан: 5)")
    ap.add_argument("--limits", default=None, help='мысалы "Ready=4,In Progress=2,Review=2"')
    args = ap.parse_args()
    {"export": cmd_export, "report": cmd_report, "selftest": cmd_selftest}[args.command](args)


if __name__ == "__main__":
    main()
