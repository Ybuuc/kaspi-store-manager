"""Create an atomic SQLite online backup, verify a separate restore, rotate 30-day files.

Run on an isolated local demo: docker compose exec -T app python /app/ops/lz11/backup_rotate.py.
The restored copy is NEVER substituted for the live database.
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


DB = Path(os.environ.get("KSM_DB_PATH", "/app/instance/kaspi-store-manager.sqlite3"))
DEST = Path(os.environ.get("KSM_BACKUP_DIR", "/app/instance/backups"))
KEEP_DAYS = 30
SAFE_BACKUP = re.compile(r"^ksm-daily-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite3$")


def fingerprint(connection):
    """Hash all rows without publishing any user/password data."""
    h = hashlib.sha256()
    tables = ("users", "products", "orders", "audit_log")
    counts = {}
    for table in tables:
        try:
            cur = connection.execute(f"SELECT * FROM {table} ORDER BY id")
            count = 0
            for row in cur:
                h.update(repr(tuple(row)).encode("utf-8"))
                h.update(b"\n")
                count += 1
            counts[table] = count
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"Required table missing: {table}") from exc
    return h.hexdigest(), counts


def checked(conn):
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("SQLite foreign key check failed")


def main():
    if not DB.is_file():
        raise RuntimeError("Database not found; initialize the disposable demo first")
    DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
    if DEST.is_symlink():
        raise RuntimeError("Refuse to write backups through a symlink")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"ksm-daily-{stamp}-{secrets.token_hex(4)}.sqlite3"
    output = DEST / name
    temp_output = DEST / f".{name}.pending"

    try:
        with closing(sqlite3.connect(str(DB), timeout=30)) as source:
            source.execute("PRAGMA busy_timeout=30000")
            with closing(sqlite3.connect(str(temp_output))) as backup:
                source.backup(backup, pages=100, sleep=0.05)
                checked(backup)
                digest, counts = fingerprint(backup)
        os.chmod(temp_output, 0o600)
        temp_output.replace(output)

        with tempfile.TemporaryDirectory(prefix="ksm-restore-check-") as directory:
            restored = Path(directory) / "restored.sqlite3"
            with closing(sqlite3.connect(str(output))) as backup:
                with closing(sqlite3.connect(str(restored))) as destination:
                    backup.backup(destination)
                    checked(destination)
                    restored_digest, restored_counts = fingerprint(destination)
            if digest != restored_digest or counts != restored_counts:
                raise RuntimeError("Restored snapshot differs from backup")

        cutoff = time.time() - KEEP_DAYS * 86400
        rotated = 0
        for candidate in DEST.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if SAFE_BACKUP.fullmatch(candidate.name) and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                rotated += 1

        print(json.dumps({
            "status": "PASS",
            "type": "synthetic SQLite backup + separate restore",
            "backup": name,
            "integrity_check": "ok",
            "restore_fingerprint_match": True,
            "retention_days": KEEP_DAYS,
            "rotated_count": rotated,
            "tables_row_counts": counts,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))
    finally:
        temp_output.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
