"""Synthetic uptime monitor: never claim this as a production incident."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uptime_kuma_api import UptimeKumaApi, MonitorType

COMPOSE = ["docker", "compose", "-f", "ops/lz11/compose.yaml"]
EVIDENCE = Path("reports/lz11/uptime-kuma-drill.json")


def now():
    return datetime.now(timezone.utc).isoformat()


def status_code(status):
    return int(getattr(status, "value", status))


def wait_for(api, monitor_id, status, after_id=0, timeout=115):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        beats = api.get_monitor_beats(monitor_id, 1)
        matches = [x for x in beats if x.get("id", 0) > after_id
                   and status_code(x.get("status", -1)) == status]
        if matches:
            x = max(matches, key=lambda item: item["id"])
            return {"id": x["id"], "time": x.get("time"), "status": status,
                    "message": x.get("msg")}
        time.sleep(5)
    raise AssertionError(f"No Kuma heartbeat status={status} after id={after_id}")


def main():
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"type": "synthetic-CI-drill", "real_user_impact": False,
                "started_at": now(), "checks": {}}
    stopped = False
    try:
        with UptimeKumaApi(os.environ.get("KUMA_URL", "http://127.0.0.1:3001"), timeout=25) as api:
            user = os.environ.get("KUMA_ADMIN_USER", "lz11-ci")
            password = os.environ["KUMA_ADMIN_PASSWORD"]
            # This is a new disposable CI volume: no existing admin to check.
            # Kuma 2 emits 'setup' before it handles needSetup reliably.
            result_setup = api.setup(user, password)
            if result_setup.get('ok') is False:
                raise RuntimeError('Kuma setup rejected initial administrator')
            api.login(user, password)
            result = api.add_monitor(type=MonitorType.HTTP,
                                     name="KSM login (CI synthetic)",
                                     url=os.environ.get("KSM_MONITOR_URL", "http://app:5000/login"),
                                     interval=20, retryInterval=20, maxretries=0)
            mid = result.get("monitorID", result.get("monitorId"))
            if mid is None:
                raise AssertionError("Kuma monitor was not created")
            evidence["monitor_id"] = mid
            up = wait_for(api, mid, 1)
            evidence["checks"]["initial_up"] = up
            print("Kuma monitor UP")
            subprocess.run(COMPOSE + ["stop", "app"], check=True)
            stopped = True
            evidence["injected_outage_at"] = now()
            down = wait_for(api, mid, 0, after_id=up["id"])
            evidence["checks"]["down"] = down
            print("Kuma monitor DOWN")
            subprocess.run(COMPOSE + ["start", "app"], check=True)
            stopped = False
            recovered = wait_for(api, mid, 1, after_id=down["id"])
            evidence["checks"]["recovered_up"] = recovered
            print("Kuma monitor recovered UP")
            evidence["result"] = "PASS"
    except Exception as exc:
        evidence["result"] = "FAIL"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if stopped:
            subprocess.run(COMPOSE + ["start", "app"], check=False)
        evidence["finished_at"] = now()
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Monitor drill result:", evidence["result"])


if __name__ == "__main__":
    main()
