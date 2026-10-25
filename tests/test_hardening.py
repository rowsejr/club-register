"""Production hardening: security headers, friendly error pages, health checks, backups that are proved restorable, and accessibility basics."""

import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from helpers import login
from sqlalchemy import select

from app import backup, hardening, jobs
from app.db import DATABASE_URL, SessionLocal
from app.main import app
from app.models import Contact, JobRun, Member, Role


def test_hsts_only_in_production(monkeypatch) -> None:
    client = TestClient(app)
    assert "strict-transport-security" not in client.get("/healthz").headers
    monkeypatch.setenv("CLUB_ENV", "production")
    assert "max-age=31536000" in client.get("/healthz").headers["strict-transport-security"]


def test_no_page_needs_an_inline_script_or_handler() -> None:
    """The policy allows only scripts from this site, so nothing may rely on inline scripts or on* handlers."""
    from pathlib import Path

    bad = []
    for path in Path("app/templates").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", text):
            bad.append((path.name, match.group(0)))
        for match in re.finditer(r"\son(click|change|submit|load|error|input)=", text):
            bad.append((path.name, match.group(0)))
    assert not bad, bad


def test_browsers_get_a_friendly_page_and_api_clients_get_json() -> None:
    client = TestClient(app)
    page = client.get("/no-such-page", headers={"accept": "text/html"})
    assert page.status_code == 404 and "We could not find that page" in page.text and "<main" in page.text
    assert client.get("/no-such-page", headers={"accept": "application/json"}).json() == {"detail": "Not Found"}
    wrong = client.post("/healthz", headers={"accept": "text/html"})
    assert wrong.status_code == 405 and "not something this page can do" in wrong.text


def test_a_crash_shows_a_reference_not_a_stack_trace(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(hardening, "_aware", boom)
    client = TestClient(app, raise_server_exceptions=False)
    page = client.get("/readyz", headers={"accept": "text/html"})
    assert page.status_code == 500 and "reference" in page.text and "secret internals" not in page.text and "Traceback" not in page.text
    api = client.get("/readyz")
    assert api.status_code == 500 and api.json()["detail"] == "Internal server error" and re.fullmatch(r"[0-9a-f]{8}", api.json()["reference"])


def test_healthz_and_readyz() -> None:
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    cold = client.get("/readyz")
    assert cold.status_code == 200 and cold.json()["status"] == "degraded" and len(cold.json()["problems"]) == 2
    assert client.get("/readyz?strict=1").status_code == 503
    with SessionLocal() as db:
        now = datetime.now(UTC)
        db.add(JobRun(job="weekly_digests", started_at=now, finished_at=now, status="ok", dry_run=False))
        db.add(JobRun(job="nightly_backup", started_at=now, finished_at=now, status="ok", dry_run=False))
        db.commit()
    healthy = client.get("/readyz?strict=1")
    assert healthy.status_code == 200 and healthy.json()["status"] == "ok" and healthy.json()["problems"] == []
    with SessionLocal() as db:
        for run in db.scalars(select(JobRun).where(JobRun.job == "nightly_backup")):
            run.finished_at = datetime.now(UTC) - timedelta(hours=40)
        db.commit()
    stale = client.get("/readyz?strict=1")
    assert stale.status_code == 503 and "no successful backup" in stale.json()["problems"][0]


# --------------------------------------------------------------------------- backups


@pytest.fixture
def populated():
    with SessionLocal() as db:
        member = Member(name="Ann Sailor", email="ann@example.org", membership_type="Single", renewal_status="active", active=True)
        db.add(member)
        db.flush()
        db.add(Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id))
        db.commit()


def test_a_backup_is_verified_and_a_restore_drill_compares_it_with_the_live_data(populated, tmp_path) -> None:
    snapshot = backup.backup(DATABASE_URL, tmp_path)
    report = backup.verify(snapshot)
    assert report["ok"] and report["integrity"] == "ok" and report["counts"]["members"] == 1
    with SessionLocal() as db:
        db.add(Member(name="Bob New", email="bob@example.org", membership_type="Single", renewal_status="active", active=True))
        db.commit()
    drill = backup.restore_drill(DATABASE_URL, snapshot)
    assert drill["restored"] and drill["rows_since_backup"] == {"members": 1}
    assert backup.latest(tmp_path) == snapshot


def test_a_damaged_or_missing_backup_fails_the_check(tmp_path) -> None:
    junk = tmp_path / "club-20260101-000000.db"
    junk.write_bytes(b"this is not a database at all" * 50)
    assert not backup.verify(junk)["ok"] and "cannot be read" in backup.verify(junk)["problem"]
    assert not backup.verify(tmp_path / "missing.db")["ok"]
    empty = tmp_path / "club-empty.db"
    sqlite3.connect(empty).close()
    assert "no members table" in backup.verify(empty)["problem"]
    assert backup.restore_drill(DATABASE_URL, junk)["restored"] is False


def test_the_nightly_job_verifies_copies_off_the_machine_and_fails_loudly(populated, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLUB_BACKUP_DIR", str(tmp_path / "b"))
    with SessionLocal() as db:
        run = jobs.run_job(db, jobs.JOBS["nightly_backup"], datetime.now(UTC).date(), False)
        assert run.status == "ok" and run.summary["verified"] is True and run.summary["offsite"] == "not configured"
    copied = tmp_path / "copy-target"
    copied.mkdir()
    script = tmp_path / "offsite.py"
    script.write_text("import shutil, sys\nshutil.copy(sys.argv[1], sys.argv[2])\n", encoding="utf-8")
    monkeypatch.setenv("CLUB_BACKUP_COMMAND", f"{sys.executable.replace(chr(92), '/')} {script.as_posix()} {{file}} {copied.as_posix()}")
    with SessionLocal() as db:
        run = jobs.run_job(db, jobs.JOBS["nightly_backup"], datetime.now(UTC).date(), False)
        assert run.status == "ok" and run.summary["offsite"] == "copied"
    assert len(list(copied.glob("club-*.db"))) == 1
    monkeypatch.setenv("CLUB_BACKUP_COMMAND", "definitely-not-a-real-command {file}")
    with SessionLocal() as db:
        assert jobs.run_job(db, jobs.JOBS["nightly_backup"], datetime.now(UTC).date(), False).status == "failed"


# --------------------------------------------------------------------------- accessibility basics


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self.labelled: set[str] = set()
        self.inputs: list[dict] = []
        self.in_label = 0
        self.h1 = 0
        self.main = False
        self.skip = False
        self.title = False
        self.lang = False
        self.in_title = False
        self.button_depth: list[dict] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001, ANN201
        a = dict(attrs)
        if tag == "html" and a.get("lang"):
            self.lang = True
        if tag == "title":
            self.in_title = True
        if tag == "main":
            self.main = True
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and a.get("class") == "skip-link":
            self.skip = True
        if tag == "img" and "alt" not in a:
            self.problems.append("img without alt")
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.labelled.add(a["for"])
        if tag in {"input", "select", "textarea"} and a.get("type") not in {"hidden", "submit", "button"}:
            self.inputs.append({**a, "in_label": bool(self.in_label)})

    def handle_endtag(self, tag):  # noqa: ANN001, ANN201
        if tag == "label":
            self.in_label -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):  # noqa: ANN001, ANN201
        if self.in_title and data.strip():
            self.title = True

    def result(self) -> list[str]:
        out = list(self.problems)
        if not self.lang:
            out.append("no lang")
        if not self.title:
            out.append("no title")
        if not self.main:
            out.append("no main landmark")
        if not self.skip:
            out.append("no skip link")
        if self.h1 != 1:
            out.append(f"{self.h1} h1 headings")
        for i in self.inputs:
            if not (i["in_label"] or i.get("aria-label") or i.get("aria-labelledby") or (i.get("id") and i["id"] in self.labelled) or i.get("title")):
                out.append(f"unlabelled {i.get('type', 'field')} name={i.get('name')}")
        return out


PUBLIC = ["/", "/login", "/membership", "/join-us", "/courses", "/whats-on", "/join", "/privacy"]
STAFF = ["/app", "/app/members", "/app/members/new", "/app/contacts", "/app/finance", "/app/finance/new", "/app/finance/bank", "/app/events", "/app/events/new", "/app/training", "/app/boats", "/app/season", "/app/admin/audit", "/app/admin/year-end", "/app/admin/jobs", "/app/enquiries", "/app/mailings"]


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_meet_the_basics(path) -> None:
    r = TestClient(app).get(path)
    assert r.status_code == 200, path
    parser = _Audit()
    parser.feed(r.text)
    assert parser.result() == [], (path, parser.result())


def test_staff_pages_meet_the_basics() -> None:
    admin = login("admin@example.org", Role.ADMIN)
    failures = {}
    for path in STAFF:
        r = admin.get(path)
        assert r.status_code == 200, (path, r.status_code)
        parser = _Audit()
        parser.feed(r.text)
        if parser.result():
            failures[path] = parser.result()
    assert not failures, failures


def test_every_page_carries_security_headers_and_a_strict_policy() -> None:
    client = TestClient(app)
    for path in ("/", "/login", "/membership", "/whats-on", "/healthz"):
        r = client.get(path)
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY", path
        assert "script-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"], path
        assert r.headers["referrer-policy"] and r.headers["permissions-policy"]
    signed_in = login("admin@example.org", Role.ADMIN)
    assert signed_in.get("/app").headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/membership").headers or "no-store" not in client.get("/membership").headers["cache-control"]
