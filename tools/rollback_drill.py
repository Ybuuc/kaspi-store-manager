"""Isolated rollback rehearsal over real HTTP and disposable SQLite fixtures."""
import argparse
import hashlib
import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def rows(path):
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                for table in ("users", "products", "orders", "audit_log")}


def backup(source, destination):
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def status(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return None


def start(directory, database, port, logfile, fault=False):
    code = (
        "import os; from app import create_app; from werkzeug.serving import make_server; "
        "a=create_app({'DATABASE':os.environ['DRILL_DATABASE']}); "
        + ("a.before_request(lambda: ('Controlled drill failure',503)); " if fault else "")
        + "make_server('127.0.0.1',int(os.environ['DRILL_PORT']),a).serve_forever()"
    )
    env = dict(os.environ, KSM_SECRET_KEY="disposable-rollback-drill-secret",
               DRILL_DATABASE=str(database), DRILL_PORT=str(port))
    with logfile.open("ab") as log:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=directory, env=env,
                                   stdout=log, stderr=log)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Server exited; inspect {logfile}")
        if status(f"http://127.0.0.1:{port}/login") is not None:
            return process
        time.sleep(.05)
    stop(process)
    raise RuntimeError("Server startup timeout")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def smoke(port):
    base = f"http://127.0.0.1:{port}"
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with client.open(base + "/login", timeout=5) as response:
        assert response.status == 200
    with client.open(base + "/products", timeout=5) as response:
        assert response.geturl().endswith("/login")
    data = urllib.parse.urlencode({"username": "drill", "password": "drill-only-password"}).encode()
    with client.open(base + "/login", data=data, timeout=5) as response:
        assert not response.geturl().endswith("/login")
    with client.open(base + "/products", timeout=5) as response:
        assert "DRILL-SKU" in response.read().decode()
    with client.open(base + "/orders", timeout=5) as response:
        assert response.status == 200
    # Failed-attempt counters can be reset by login; fixtures start at zero.
    return ["login 200", "anonymous catalogue redirects to login", "employee login",
            "catalogue contains DRILL-SKU", "orders 200"]


def drill(work, report):
    root = Path(__file__).resolve().parents[1]
    if work.exists():
        raise ValueError("Choose a new --work-dir: never overwrite a previous rehearsal")
    work.mkdir(parents=True)
    old_sha = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=root, text=True).strip()
    candidate_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    previous, candidate = work / "previous", work / "candidate"
    previous.mkdir()
    candidate.mkdir()
    old_zip = work / "previous.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--output={old_zip}", old_sha,
                    "app.py", "templates", "static", "requirements.txt"], cwd=root, check=True)
    with zipfile.ZipFile(old_zip) as archive:
        archive.extractall(previous)
    subprocess.run([sys.executable, "tools/build_release.py"], cwd=root, check=True)
    current_zip = root / "dist" / f"kaspi-store-manager-{candidate_sha[:12]}.zip"
    with zipfile.ZipFile(current_zip) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest
                   for name, digest in manifest["files"].items())
        archive.extractall(candidate)
    # All data below are newly created fixtures, never a user database.
    sys.path.insert(0, str(previous))
    from app import create_app, hash_password
    database, copy = work / "fixture.sqlite3", work / "backup.sqlite3"
    app = create_app({"SECRET_KEY": "fixture-only", "DATABASE": str(database)})
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)",
                   ("drill", hash_password("drill-only-password")))
        db.execute("INSERT INTO products(name,sku,price_cents,stock) VALUES ('Drill product','DRILL-SKU',12500,7)")
        db.execute("INSERT INTO orders(ordered_on,amount_cents,status) VALUES ('2026-11-03',12500,'Жаңа')")
    backup(database, copy)
    initial = rows(database)
    assert rows(copy) == initial
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = None
    try:
        process = start(previous, database, port, work / "previous.log")
        smoke(port)
        stop(process)
        process = start(candidate, database, port, work / "candidate.log")
        smoke(port)
        # A post-backup order must survive a code-only rollback.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("INSERT INTO orders(ordered_on,amount_cents,status) VALUES ('2026-11-03',25000,'Жаңа')")
        expected = rows(database)
        stop(process)
        process = start(candidate, database, port, work / "fault.log", fault=True)
        assert status(f"http://127.0.0.1:{port}/login") == 503
        detected = datetime.now(timezone.utc).isoformat()
        timer = time.perf_counter()
        stop(process)
        process = start(previous, database, port, work / "rollback-code.log")
        checks = smoke(port)
        assert rows(database) == expected
        code_seconds = time.perf_counter() - timer
        stop(process)
        process = None
        # Separate restore scenario, with no accepted writes after its backup.
        damaged = work / "restore-fixture.sqlite3"
        shutil.copy2(copy, damaged)
        damaged.write_bytes(b"controlled fixture corruption for rollback drill")
        timer = time.perf_counter()
        shutil.copy2(damaged, work / "preserved-damaged.sqlite3")
        shutil.copy2(copy, damaged)  # All connections/processes are stopped.
        process = start(previous, damaged, port, work / "rollback-db.log")
        smoke(port)
        assert rows(damaged) == initial
        restore_seconds = time.perf_counter() - timer
    finally:
        if process is not None and process.poll() is None:
            stop(process)
    output = {"kind": "isolated educational rollback drill; NOT a production incident",
              "timestamp_utc": detected, "previous_sha": old_sha, "candidate_sha": candidate_sha,
              "code_rollback_seconds": round(code_seconds, 3),
              "database_restore_seconds": round(restore_seconds, 3), "smoke": checks,
              "sqlite_integrity": "ok", "post_backup_order_preserved_on_code_rollback": True,
              "restored_data_match_backup": True, "fixture_orders_before": len(initial["orders"]),
              "fixture_orders_after_code_rollback": len(expected["orders"]),
              "target_seconds": 900, "passed": code_seconds < 900 and restore_seconds < 900,
              "limitations": ["Both versions use the same app.py; current release changes CI/docs/tools",
                              "Same local Python environment, not a separate production host",
                              "Automated smoke only; no 15-minute observation or manual acceptance",
                              "Database-restore scenario has no post-backup writes; lost-write reconciliation not rehearsed"],
              "work_directory": str(work)}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    drill(args.work_dir.resolve(), args.report.resolve())
