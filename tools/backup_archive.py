"""Consistent backups of the entire single archive database, with retention."""
import argparse
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def backup(database, output, retention_days=30):
    database, output = Path(database).resolve(), Path(output).resolve()
    if not database.is_file():
        raise ValueError('Database must exist; run init-db first')
    if retention_days < 1:
        raise ValueError('Retention must be at least one day')
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = output / f'archive-{stamp}.sqlite3'
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity failure')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for candidate in output.glob('archive-*.sqlite3'):
        if datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc) < cutoff:
            candidate.unlink()
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('app.db'))
    parser.add_argument('--output', type=Path, default=Path('backups'))
    parser.add_argument('--retention-days', type=int, default=30)
    parser.add_argument('--interval-seconds', type=int, default=0, help='0: once; 86400: daily while running')
    args = parser.parse_args()
    if args.interval_seconds < 0:
        parser.error('Interval must be nonnegative')
    while True:
        print(backup(args.database, args.output, args.retention_days), flush=True)
        if not args.interval_seconds:
            return
        time.sleep(args.interval_seconds)


if __name__ == '__main__':
    main()
