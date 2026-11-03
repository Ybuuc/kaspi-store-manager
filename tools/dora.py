"""Export real GitHub history; calculate four DORA metrics without CI/deploy proxies."""
import argparse
import json
import os
import statistics
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHub:
    def __init__(self, repo):
        self.base = f"https://api.github.com/repos/{repo}"
        self.requests = []

    def get(self, path):
        url = self.base + path
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "ksm-dora-lz10"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            result = json.load(response)
        self.requests.append({"url": url, "collected_at": datetime.now(timezone.utc).isoformat()})
        return result

    def pages(self, path, key=None):
        result = []
        page = 1
        while True:
            payload = self.get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            items = payload[key] if key else payload
            result.extend(items)
            if len(items) < 100:
                return result
            page += 1


def export(repo, output, days, end=None):
    api = GitHub(repo)
    end = stamp(end) if end else datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    metadata = api.get("")
    deployments = api.pages("/deployments?environment=production")
    for deployment in deployments:
        deployment["statuses"] = api.pages(f"/deployments/{deployment['id']}/statuses")
    # Keep full responses for an auditable, reproducible snapshot.
    data = {
        "repository": repo, "start": start.isoformat(), "end": end.isoformat(),
        "metadata": metadata, "deployments": deployments,
        "releases": api.pages("/releases"),
        "pull_requests": api.pages("/pulls?state=all&sort=created&direction=desc"),
        "issues": api.pages("/issues?state=all"),
        "runs": api.pages("/actions/runs", "workflow_runs"),
        "commits": api.pages(f"/commits?sha={metadata['default_branch']}&since={start.isoformat()}&until={end.isoformat()}"),
        "deployment_changes": {},
    }
    successful = successful_deployments(data)
    previous = None
    for deployed, deployment in successful:
        if previous and start <= deployed < end:
            # Compare API pagination: each page contains another batch of commits.
            commits = (api.pages(f"/compare/{previous['sha']}...{deployment['sha']}", "commits")
                       if deployment["sha"] != previous["sha"] else [])
            data["deployment_changes"][str(deployment["id"])] = commits
        previous = deployment
    data["requests"] = api.requests
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(data['commits'])} commits, {len(deployments)} production deployments to {output}")


def successful_deployments(data):
    values = []
    for deployment in data["deployments"]:
        successes = [stamp(s["created_at"]) for s in deployment["statuses"] if s["state"] == "success"]
        if successes:
            values.append((min(successes), deployment))
    return sorted(values, key=lambda pair: (pair[0], pair[1]["id"]))


def level(metric, value):
    if value is None:
        return "Недостаточно данных"
    # Historical 2021 benchmark, not a current universal standard.
    if metric == "df":
        return "Elite" if value > 1 else "High" if value >= 1/30 else "Medium" if value >= 1/183 else "Low"
    if metric == "lt":
        return "Elite" if value < 24 else "High" if value < 168 else "Medium" if value < 4380 else "Low"
    if metric == "cfr":
        return "Elite / High / Medium" if value <= 15 else "Low / вне диапазона 2021"
    return "Elite" if value < 1 else "High" if value < 24 else "Medium" if value < 168 else "Low"


def calculate(data, incident_file):
    start, end = stamp(data["start"]), stamp(data["end"])
    deployments = [(t, d) for t, d in successful_deployments(data) if start <= t < end]
    ids = {d["id"] for _, d in deployments}
    lead_times = []
    missing = []
    seen = set()
    for deployed, deployment in deployments:
        changes = data["deployment_changes"].get(str(deployment["id"]))
        if changes is None:
            missing.append(deployment["id"])
            continue
        for commit in changes:
            if commit["sha"] in seen or len(commit.get("parents", [])) > 1:
                continue
            hours = (deployed - stamp(commit["commit"]["committer"]["date"])).total_seconds()/3600
            if hours < 0:
                raise ValueError("Commit time is after its production deployment")
            seen.add(commit["sha"])
            lead_times.append(hours)
    incident_data = json.loads(incident_file.read_text(encoding="utf-8"))
    coverage = (incident_data["complete"] and stamp(incident_data["start"]) <= start
                and stamp(incident_data["end"]) >= end)
    issues = {i["number"]: i for i in data["issues"] if "pull_request" not in i}
    failures, recovery, unresolved = set(), [], 0
    for mapping in incident_data["incidents"]:
        if mapping["deployment_id"] not in ids:
            continue
        issue = issues[mapping["issue_number"]]
        if "incident" not in {label["name"] for label in issue["labels"]}:
            raise ValueError("Incident mapping must reference a real incident-labelled issue")
        failures.add(mapping["deployment_id"])
        # Incident issue creation = detection; closure = verified recovery by convention.
        if issue["closed_at"] and stamp(issue["closed_at"]) < end:
            recovery.append((stamp(issue["closed_at"]) - stamp(issue["created_at"])).total_seconds()/3600)
        else:
            unresolved += 1
    values = {
        "df": len(deployments)/((end-start).total_seconds()/86400),
        "lt": statistics.median(lead_times) if lead_times and not missing else None,
        "cfr": 100*len(failures)/len(ids) if ids and coverage else None,
        "mttr": statistics.mean(recovery) if recovery and coverage else None,
    }
    prs = [p for p in data["pull_requests"] if p["merged_at"] and start <= stamp(p["merged_at"]) < end]
    runs = [r for r in data["runs"] if start <= stamp(r["created_at"]) < end]
    return {
        "repository": data["repository"], "start": data["start"], "end": data["end"],
        "metrics": {key: {"value": value, "level": level(key, value)} for key, value in values.items()},
        "evidence": {"production_deployments": len(ids), "lead_time_samples": len(lead_times),
                     "deployments_without_baseline": missing, "incident_coverage_complete": coverage,
                     "failed_deployments": len(failures), "recovered_incidents": len(recovery),
                     "unresolved_incidents": unresolved, "merged_prs": len(prs),
                     "pr_cycle_hours_median": statistics.median(
                         [(stamp(p['merged_at'])-stamp(p['created_at'])).total_seconds()/3600 for p in prs]) if prs else None,
                     "commits": len(data["commits"]), "ci_runs": len(runs),
                     "ci_successes": sum(r["conclusion"] == "success" for r in runs),
                     "releases": len(data["releases"])},
    }


def report(source, incidents, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = json.loads(source.read_text(encoding="utf-8"))
    result = calculate(data, incidents)
    output.mkdir(parents=True, exist_ok=True)
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), facecolor="#0f172a")
    labels = {"df": ("Deployment frequency", "deployments / day"),
              "lt": ("Lead time for changes", "hours, median"),
              "cfr": ("Change failure rate", "% of production deployments"),
              "mttr": ("Time to restore service", "hours, mean")}
    for ax, (key, (title, unit)) in zip(axes.flat, labels.items()):
        metric = result["metrics"][key]
        ax.set_facecolor("#1e293b")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(title, color="white", fontsize=16, pad=16)
        value = metric["value"]
        ax.text(.5, .62, "N/A" if value is None else f"{value:.3f}", ha="center", transform=ax.transAxes,
                color="#38bdf8" if value is not None else "#fbbf24", fontsize=38, weight="bold")
        ax.text(.5, .41, unit, ha="center", transform=ax.transAxes, color="white")
        ax.text(.5, .22, metric["level"], ha="center", transform=ax.transAxes, color="#cbd5e1")
    evidence = result["evidence"]
    fig.suptitle(f"Kaspi Store Manager | DORA | {data['start'][:10]} — {data['end'][:10]}", color="white", fontsize=18)
    fig.text(.5, .055, f"Real GitHub API: {evidence['production_deployments']} production deployments | "
             f"{evidence['merged_prs']} merged PRs | {evidence['ci_runs']} CI runs\n"
             "N/A = insufficient evidence. CI success and PR merge are not production deployments.",
             ha="center", color="#cbd5e1", fontsize=11)
    fig.subplots_adjust(top=.86, bottom=.16, hspace=.35, wspace=.15)
    fig.savefig(output / "dashboard.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "report"])
    parser.add_argument("--repo", default="Ybuuc/kaspi-store-manager")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--end", help="Exclusive UTC boundary, ISO 8601")
    parser.add_argument("--source", type=Path, default=Path("docs/ci-cd/lz10/data/github.json"))
    parser.add_argument("--incidents", type=Path, default=Path("docs/ci-cd/lz10/data/incidents.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/ci-cd/lz10/reports"))
    args = parser.parse_args()
    if args.command == "export":
        if args.days <= 0:
            parser.error("--days must be positive")
        export(args.repo, args.source, args.days, args.end)
    else:
        report(args.source, args.incidents, args.output)


if __name__ == "__main__":
    main()
