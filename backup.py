"""Encrypted database backup and restore.

  python backup.py backup  --out eliberia-2026-11-16.backup      (needs DATABASE_URL, BACKUP_KEY)
  python backup.py inspect --file eliberia-2026-11-16.backup     (row counts only; needs BACKUP_KEY)
  python backup.py restore --file eliberia-2026-11-16.backup --yes --replace
      (into a NEW deployment: start the app once against the new database so its tables exist;
       --replace wipes that new database's seeded rows before loading the backup)

Rows are copied exactly as stored, so personal data that is encrypted in the database stays encrypted
in the backup, and the whole file is then encrypted again with BACKUP_KEY. Keep BACKUP_KEY and the
application's DATA_KEY (or SECRET_KEY) somewhere safe and separate: without them a backup is useless.
Uploaded files are not in this backup - keep them in durable object storage (STORAGE_BACKEND=s3).
"""
import argparse
import base64
import datetime as dt
import decimal
import gzip
import hashlib
import json
import os
import sys

from cryptography.fernet import Fernet
from sqlalchemy import MetaData, create_engine, inspect, text

FORMAT = "eliberia-backup-1"


def _fernet():
    key = os.environ.get("BACKUP_KEY")
    if not key or len(key) < 16:
        sys.exit("Set BACKUP_KEY (at least 16 characters) - the backup is encrypted with it.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    return create_engine(url)


def _jsonable(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, memoryview)):
        return base64.b64encode(bytes(v)).decode()
    return v


def _ordered_tables(engine):
    md = MetaData()
    md.reflect(engine)
    return [t.name for t in md.sorted_tables]  # parents before children


def backup(out):
    engine = _engine()
    data = {"format": FORMAT, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "tables": {}}
    with engine.connect() as c:
        for name in _ordered_tables(engine):
            rows = c.execute(text(f'SELECT * FROM "{name}"')).mappings().all()
            data["tables"][name] = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    blob = _fernet().encrypt(gzip.compress(json.dumps(data).encode()))
    with open(out, "wb") as f:
        f.write(blob)
    counts = {k: len(v) for k, v in data["tables"].items()}
    print(f"Backup written to {out} ({len(blob) / 1024:.1f} KB): {counts}")


def _load(path):
    with open(path, "rb") as f:
        data = json.loads(gzip.decompress(_fernet().decrypt(f.read())))
    if data.get("format") != FORMAT:
        sys.exit("Not an eLiberia backup file")
    return data


def inspect_file(path):
    data = _load(path)
    print(f"Backup from {data['created_at']}")
    for k, v in data["tables"].items():
        print(f"  {k}: {len(v)} rows")


def restore(path, yes, replace=False):
    if not yes:
        sys.exit("Restoring writes to the database in DATABASE_URL. Re-run with --yes to confirm.")
    data = _load(path)
    engine = _engine()
    have = set(inspect(engine).get_table_names())
    missing = [t for t in data["tables"] if t not in have]
    if missing:
        sys.exit(f"Start the application once against the new database first (missing tables: {missing})")
    with engine.begin() as c:
        filled = [n for n in data["tables"] if c.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar()]
        if filled and not replace:
            sys.exit(f"These tables already have rows: {filled}. Re-run with --replace to wipe the target "
                     f"database first (only ever do this on a new, empty deployment).")
        for name in reversed(list(data["tables"])):  # children first
            c.execute(text(f'DELETE FROM "{name}"'))
        for name, rows in data["tables"].items():
            for r in rows:
                cols = ", ".join(f'"{k}"' for k in r)
                vals = ", ".join(f":{k}" for k in r)
                c.execute(text(f'INSERT INTO "{name}" ({cols}) VALUES ({vals})'), r)
        if engine.dialect.name == "postgresql":  # move id sequences past the restored rows
            for name in data["tables"]:
                c.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{name}\"', 'id'), "
                               f"COALESCE((SELECT MAX(id) FROM \"{name}\"), 1))"))
    print("Restore complete.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["backup", "inspect", "restore"])
    ap.add_argument("--out", default=f"eliberia-{dt.date.today().isoformat()}.backup")
    ap.add_argument("--file")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--replace", action="store_true", help="wipe the target database before restoring")
    a = ap.parse_args()
    if a.command == "backup":
        backup(a.out)
    elif not a.file:
        sys.exit("--file is required")
    elif a.command == "inspect":
        inspect_file(a.file)
    else:
        restore(a.file, a.yes, a.replace)
