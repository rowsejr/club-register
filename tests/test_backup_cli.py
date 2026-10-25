import sqlite3
from pathlib import Path

from app.backup import backup, restore


def _make_db(path: Path, value: str) -> str:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()
    return f"sqlite:///{path.as_posix()}"


def _value(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def test_backup_destroy_restore(tmp_path: Path) -> None:
    db = tmp_path / "live.db"
    url = _make_db(db, "precious")
    snap = backup(url, tmp_path / "backups")
    _make_db(db, "corrupted")
    safety = restore(url, snap)
    assert _value(db) == "precious"
    assert _value(safety) == "corrupted"


