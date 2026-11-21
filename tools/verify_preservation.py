"""Compare every original row/column after additive migration without printing shop data."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def verify(original, migrated):
    result = {}
    with closing(sqlite3.connect(Path(original).resolve().as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(Path(migrated).resolve().as_uri()+'?mode=ro',uri=True)) as target:
            for table, in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                columns=[r[1] for r in source.execute(f'PRAGMA table_info("{table}")')]
                names=','.join('"'+c+'"' for c in columns)
                old=source.execute(f'SELECT {names} FROM "{table}" ORDER BY id').fetchall()
                new=target.execute(f'SELECT {names} FROM "{table}" ORDER BY id').fetchall()
                if old != new:
                    raise ValueError(f'Original field values differ in {table}; do not distribute this migration')
                result[table]=dict(rows=len(old),columns=len(columns),preserved=True,
                                   original_fields_sha256=hashlib.sha256(repr(old).encode()).hexdigest())
            integrity=target.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok':
                raise ValueError('Migrated database integrity failed')
    return dict(integrity=integrity,tables=result,all_original_fields_preserved=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original',type=Path)
    parser.add_argument('migrated',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=verify(args.original,args.migrated)
    if args.output:
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
