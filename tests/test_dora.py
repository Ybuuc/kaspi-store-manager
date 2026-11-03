"""Metric contracts: missing evidence must not become fabricated zeroes."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.dora import calculate, level


class DoraTests(unittest.TestCase):
    def snapshot(self):
        return {"repository": "test/repo", "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-11T00:00:00Z", "deployments": [], "deployment_changes": {},
                "issues": [], "pull_requests": [], "runs": [], "commits": [], "releases": []}

    def metric(self, data, complete=False, incidents=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incidents.json"
            path.write_text(json.dumps({"start": data["start"], "end": data["end"],
                                        "complete": complete, "incidents": incidents or []}), encoding="utf-8")
            return calculate(data, path)

    def deployment(self, identity=1, time="2026-10-02T12:00:00Z"):
        return {"id": identity, "sha": str(identity),
                "statuses": [{"state": "success", "created_at": time},
                             {"state": "success", "created_at": time}]}

    def test_no_production_history(self):
        metrics = self.metric(self.snapshot())["metrics"]
        self.assertEqual(metrics["df"]["value"], 0)
        for key in ("lt", "cfr", "mttr"):
            self.assertIsNone(metrics[key]["value"])

    def test_incomplete_incident_register_is_not_zero_failure_rate(self):
        data = self.snapshot()
        data["deployments"] = [self.deployment()]
        result = self.metric(data)
        self.assertEqual(result["metrics"]["df"]["value"], .1)
        self.assertIsNone(result["metrics"]["cfr"]["value"])
        self.assertIsNone(result["metrics"]["lt"]["value"])
        self.assertEqual(self.metric(data, complete=True)["metrics"]["cfr"]["value"], 0)

    def test_lead_time_counts_each_change_once_and_excludes_merge_commits(self):
        data = self.snapshot()
        data["deployments"] = [self.deployment(), self.deployment(2, "2026-10-03T12:00:00Z")]
        change = {"sha": "abc", "parents": [{}], "commit": {"committer": {"date": "2026-10-02T00:00:00Z"}}}
        merge = dict(change, sha="merge", parents=[{}, {}])
        data["deployment_changes"] = {"1": [change, merge], "2": [change]}
        result = self.metric(data)
        self.assertEqual(result["metrics"]["lt"]["value"], 12)
        self.assertEqual(result["evidence"]["lead_time_samples"], 1)

    def test_incident_recovery_and_distinct_failed_deployments(self):
        data = self.snapshot()
        data["deployments"] = [self.deployment()]
        data["issues"] = [{"number": n, "labels": [{"name": "incident"}],
                           "created_at": "2026-10-02T13:00:00Z",
                           "closed_at": "2026-10-02T15:00:00Z" if n == 1 else None} for n in (1, 2)]
        mappings = [{"issue_number": n, "deployment_id": 1} for n in (1, 2)]
        result = self.metric(data, True, mappings)
        self.assertEqual(result["metrics"]["cfr"]["value"], 100)
        self.assertEqual(result["metrics"]["mttr"]["value"], 2)
        self.assertEqual(result["evidence"]["unresolved_incidents"], 1)

    def test_exclusive_window_boundary(self):
        data = self.snapshot()
        data["deployments"] = [self.deployment(time=data["end"])]
        self.assertEqual(self.metric(data)["metrics"]["df"]["value"], 0)

    def test_historical_benchmark_boundaries(self):
        self.assertEqual(level("lt", 24), "High")
        self.assertEqual(level("mttr", 1), "High")
        self.assertEqual(level("df", 0), "Low")
