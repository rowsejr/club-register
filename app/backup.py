from __future__ import annotations

import os
import shlex
import shutil
import sqlite3
import subprocess  # noqa: S404
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def redact(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def _sqlite_path(database_url: str) -> Path:
    database = make_url(database_url).database
    if not database or database == ":memory:":
        raise ValueError("In-memory SQLite databases cannot be backed up")
    return Path(database).resolve()


def backup(database_url: str, out_dir: Path) -> Path:
    """Write a consistent, timestamped backup of the database and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        target = out_dir / f"club-{stamp}.db"
        source = sqlite3.connect(_sqlite_path(database_url))
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)  # safe while the app is running
        finally:
            dest.close()
            source.close()
        return target
    target = out_dir / f"club-{stamp}.sql"
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump not found on PATH")
    subprocess.run(  # noqa: S603
        [pg_dump, "--no-password", "--file", str(target), url.set(drivername="postgresql").render_as_string(hide_password=False)],
        check=True,
    )
    return target


def restore(database_url: str, backup_file: Path) -> Path:
    """Restore a SQLite backup, first saving the current database next to it. Returns the safety copy."""
    if make_url(database_url).get_backend_name() != "sqlite":
        raise RuntimeError("Automatic restore supports SQLite only; use psql for PostgreSQL dumps")
    target = _sqlite_path(database_url)
    safety = target.with_name(f"{target.stem}.pre-restore-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db")
    if target.exists():
        shutil.copy2(target, safety)
    shutil.copy2(backup_file, target)
    return safety


KEY_TABLES = ("members", "contacts", "invoices", "payment_transactions", "bank_entries", "audit_logs", "events", "race_series")


def backup_dir() -> Path:
    return Path(os.getenv("CLUB_BACKUP_DIR", "backups"))


def latest(folder: Path) -> Path | None:
    files = sorted(folder.glob("club-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _counts(path: Path) -> tuple[str, dict[str, int]]:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        return verdict, {t: conn.execute(f'select count(*) from "{t}"').fetchone()[0] for t in KEY_TABLES if t in tables}  # noqa: S608 - table names are constants above
    finally:
        conn.close()


def verify(backup_file: Path) -> dict:
    """Open a backup read-only, check SQLite's own integrity test passes, and count the main tables."""
    if not backup_file.exists():
        return {"file": backup_file.name, "ok": False, "problem": "the file does not exist"}
    try:
        verdict, counts = _counts(backup_file)
    except sqlite3.DatabaseError as exc:
        return {"file": backup_file.name, "ok": False, "problem": f"cannot be read as a database ({exc})"}
    ok = verdict == "ok" and "members" in counts
    return {"file": backup_file.name, "size_bytes": backup_file.stat().st_size, "integrity": verdict, "counts": counts, "ok": ok, "problem": "" if ok else ("integrity check failed" if verdict != "ok" else "it has no members table")}


def restore_drill(database_url: str, backup_file: Path) -> dict:
    """Prove a backup can be restored: copy it somewhere safe, open that copy, and compare it with the live database. Nothing live is touched."""
    report = verify(backup_file)
    if not report["ok"]:
        return {**report, "restored": False}
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "restored.db"
        shutil.copy2(backup_file, copy)
        verdict, restored = _counts(copy)
    live: dict[str, int] = {}
    if make_url(database_url).get_backend_name() == "sqlite" and _sqlite_path(database_url).exists():
        _, live = _counts(_sqlite_path(database_url))
    behind = {t: live[t] - n for t, n in restored.items() if t in live and live[t] != n}
    return {**report, "restored": verdict == "ok", "live_counts": live, "rows_since_backup": behind}


def offsite(snapshot: Path) -> str:
    """Copy a backup off this machine with the command in CLUB_BACKUP_COMMAND, e.g. ``gsutil cp {file} gs://my-bucket/``. No shell is involved."""
    template = os.getenv("CLUB_BACKUP_COMMAND", "").strip()
    if not template:
        return "not configured"
    parts = shlex.split(template, posix=os.name != "nt")  # Windows paths keep their backslashes
    args = [part.replace("{file}", str(snapshot)).strip('"') if os.name == "nt" else part.replace("{file}", str(snapshot)) for part in parts]
    done = subprocess.run(args, timeout=900, capture_output=True, text=True)  # noqa: S603
    if done.returncode != 0:
        raise RuntimeError(f"The off-server copy failed (exit {done.returncode}): {(done.stderr or done.stdout).strip()[:300]}")
    return "copied"
