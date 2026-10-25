"""Scheduled jobs, digests for each hat, email retries and unsubscribing."""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import digests, finance, jobs, mailer, mailings
from app.db import SessionLocal
from app.models import (
    BankEntry,
    Boat,
    Contact,
    JobRun,
    Member,
    NominalCode,
    OutboundEmail,
    Role,
    UserRole,
)

TODAY = date.today()


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0101", label="Membership"))
        session.commit()
        yield session


def _overdue_invoice(db, name="Ann Sailor", email="ann@example.org", days=40, reminders=True):
    member = Member(name=name, email=email, membership_type="Single Adult Sailing", renewal_status="active", active=True)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id)
    db.add(contact)
    db.flush()
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 10800, "nominal_code": "0101"}], issue_date=TODAY - timedelta(days=days + 30), terms_days=30, send_reminders=reminders)
    finance.issue(db, invoice, "t")
    db.flush()
    return contact, invoice


def _hat(db, email, hat, role=Role.COMMITTEE):
    db.add(UserRole(email=email, role=role, extra_roles=hat))
    db.flush()


def test_the_treasurers_digest_counts_money_jobs(db) -> None:
    _overdue_invoice(db)
    db.add(BankEntry(account_id=1, entry_date=TODAY, details="X", amount_minor=100, status="unmatched", fingerprint="d1"))
    db.flush()
    digest = digests.treasurer(db, TODAY)
    counts = {line.text.split(",")[0]: line.count for line in digest.lines}
    assert counts["overdue invoices"] == 1 and counts["invoices more than 30 days late"] == 1 and counts["bank lines waiting to be matched or categorised"] == 1
    subject, body = digests.render(digest, "Margate Yacht Club", "Tracy")
    assert "treasurer jobs this week" in subject and body.startswith("Hello Tracy") and "1 overdue invoices, £108.00 in all" in body and "/app/finance?filter=overdue" in body


def test_each_hat_has_a_digest_about_its_own_area(db) -> None:
    db.add(Boat(name="Wavelet", status="active", owner_contact_id=None))
    db.flush()
    assert {line.text.split(" (")[0] for line in digests.bosun(db, TODAY).todo} >= {"boats with no space"}
    assert digests.secretary(db, TODAY).empty and digests.training_officer(db, TODAY).empty
    assert [line.count for line in digests.treasurer(db, TODAY).todo] == []  # money received is informational only


def test_digests_go_to_the_hat_holders_and_empty_ones_are_not_sent(db) -> None:
    _overdue_invoice(db)
    _hat(db, "tre@example.org", "treasurer")
    _hat(db, "sec@example.org", "secretary")
    _hat(db, "tre2@example.org", "treasurer,bosun")
    db.commit()
    dry = digests.send_all(db, TODAY, dry_run=True)
    assert dry["told"] == {"treasurer": ["tre2@example.org", "tre@example.org"]} and sorted(dry["empty"]) == ["bosun", "secretary"]
    assert db.scalar(select(OutboundEmail.id).where(OutboundEmail.related_type == "digest")) is None  # rehearsal sent nothing
    digests.send_all(db, TODAY)
    mails = db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "digest")).all()
    assert sorted(m.to_address for m in mails) == ["tre2@example.org", "tre@example.org"]


def test_overdue_reminders_respect_the_invoice_switch_and_the_gap(db) -> None:
    _overdue_invoice(db)
    _overdue_invoice(db, "Bob Rower", "bob@example.org", reminders=False)
    db.commit()
    assert jobs.overdue_reminders(db, TODAY, True) == {"would_remind": ["1001"]}
    assert jobs.overdue_reminders(db, TODAY, False) == {"sent": 1, "skipped": 0}
    assert jobs.overdue_reminders(db, TODAY, False) == {"sent": 0, "skipped": 0}  # within 14 days: nothing more
    assert len(db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "invoice_reminder")).all()) == 1


def test_the_runner_runs_due_jobs_once_a_day_and_weekly_ones_once_a_week(db) -> None:
    first = jobs.run_due(db, TODAY)
    assert {r.job for r in first} == set(jobs.JOBS) and all(r.status == "ok" for r in first)
    assert jobs.run_due(db, TODAY) == []  # nothing is due a second time the same day
    tomorrow = {r.job for r in jobs.run_due(db, TODAY + timedelta(days=1))}
    daily = {n for n, j in jobs.JOBS.items() if j.schedule == "daily"}
    new_week = TODAY.isocalendar()[:2] != (TODAY + timedelta(days=1)).isocalendar()[:2]
    assert tomorrow == (set(jobs.JOBS) if new_week else daily)  # weekly jobs wait for the next week


def test_a_rehearsal_changes_nothing_and_does_not_count_as_the_days_run(db) -> None:
    _overdue_invoice(db)
    db.commit()
    runs = jobs.run_due(db, TODAY, dry_run=True, only=["overdue_reminders"])
    assert runs[0].dry_run and runs[0].summary == {"would_remind": ["1001"]}
    assert db.scalar(select(OutboundEmail.id).where(OutboundEmail.related_type == "invoice_reminder")) is None
    real = jobs.run_due(db, TODAY, only=["overdue_reminders"])
    assert real[0].summary == {"sent": 1, "skipped": 0}
    assert jobs.last_real_run(db, "overdue_reminders") == TODAY


def test_one_broken_job_does_not_stop_the_others(db, monkeypatch) -> None:
    def boom(db_, today, dry_run):
        raise RuntimeError("the mail server is down")

    monkeypatch.setitem(jobs.JOBS, "overdue_reminders", jobs.Job("overdue_reminders", "x", "daily", boom))
    runs = {r.job: r for r in jobs.run_due(db, TODAY)}
    assert runs["overdue_reminders"].status == "failed" and "mail server is down" in runs["overdue_reminders"].summary["error"]
    assert runs["retry_emails"].status == "ok"
    assert jobs.last_real_run(db, "overdue_reminders") is None  # a failed run does not count: it will be tried again


def test_the_renewal_notices_job_waits_for_the_window(db, monkeypatch) -> None:
    monkeypatch.setattr(jobs.membership, "renewal_window_months", lambda _db: 4)
    assert "has not opened" in jobs.renewal_notices(db, date(TODAY.year, 3, 1), False)["skipped"]
    assert "skipped" not in jobs.renewal_notices(db, date(TODAY.year, 10, 1), True)


def test_failed_emails_are_retried_up_to_three_times_and_attachments_wait_for_a_person(db, monkeypatch) -> None:
    plain = mailer.send_email(db, to="a@example.org", subject="Plain", body="hello")
    withfile = mailer.send_email(db, to="b@example.org", subject="Invoice", body="see attached", attachment=("x.pdf", b"%PDF", "application/pdf"))
    plain.status = withfile.status = "failed"
    db.flush()
    assert mailer.retry_failed(db, dry_run=True) == {"retried": 1, "sent": 0, "gave_up": 0, "needs_a_person": 1}
    assert plain.status == "failed" and (plain.attempts or 1) == 1  # a rehearsal changed nothing
    assert mailer.retry_failed(db) == {"retried": 1, "sent": 1, "gave_up": 0, "needs_a_person": 1}  # the sandbox "sends" it
    assert plain.status == "simulated" and plain.attempts == 2
    plain.status, plain.attempts = "failed", 3
    db.flush()
    assert mailer.retry_failed(db)["gave_up"] == 1


def test_unsubscribing_stops_mailings_and_only_the_right_link_works(db) -> None:
    contact, _ = _overdue_invoice(db)
    db.commit()
    token = mailings.unsubscribe_token(db, contact.id)
    assert mailings.contact_for_token(db, token).id == contact.id
    assert mailings.contact_for_token(db, f"{contact.id}.000000000000000000000000") is None and mailings.contact_for_token(db, "garbage") is None
    mailing = mailings.save_draft(db, None, subject="News", body="Hello", audience_params={}, actor="t")
    assert any("unsubscribe/" + token in r.body for r in mailings.preview(db, mailing).recipients)
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    anonymous = TestClient(app)
    assert "Unsubscribe me" in anonymous.get(f"/unsubscribe/{token}").text
    assert "You are unsubscribed" in anonymous.post(f"/unsubscribe/{token}").text
    assert "already unsubscribed" in anonymous.get(f"/unsubscribe/{token}").text
    assert "not valid" in anonymous.get("/unsubscribe/1.bad").text and "not valid" in anonymous.post("/unsubscribe/1.bad").text
    with SessionLocal() as db2:
        assert db2.get(Contact, contact.id).suppressed is True
    db.expire_all()
    assert "Ann Sailor: asked not to be emailed" in mailings.preview(db, mailing).skipped


def test_the_admin_jobs_screen_rehearses_and_runs_and_is_admin_only(db) -> None:
    admin = login("boss@example.org", Role.ADMIN)
    page = admin.get("/app/admin/jobs")
    assert page.status_code == 200 and "Scheduled jobs" in page.text and "Remind people about overdue invoices" in page.text
    token = csrf(admin, "/app/admin/jobs")
    rehearsal = admin.post("/app/admin/jobs/overdue_reminders/run", data={"csrf": token, "mode": "rehearse"}, follow_redirects=True)
    assert "rehearsal ok" in rehearsal.text and "rehearsal" in rehearsal.text
    assert admin.post("/app/admin/jobs/nope/run", data={"csrf": token}).status_code == 404
    with SessionLocal() as db2:
        assert db2.scalar(select(JobRun.dry_run)) is True
    treasurer = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    assert treasurer.get("/app/admin/jobs").status_code == 403


def test_the_command_line_runner_lists_unknown_jobs_and_rehearses(tmp_path) -> None:
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'cli.db'}", "PATH": "", "SYSTEMROOT": "C:\\Windows"}
    root = Path(__file__).resolve().parent.parent
    ok = subprocess.run([sys.executable, "-I", "-m", "app.cli", "run-jobs", "--dry-run", "--only", "overdue_reminders"], cwd=root, env=env, capture_output=True, text=True)  # noqa: S603
    assert ok.returncode == 0 and "(rehearsal) overdue_reminders: ok" in ok.stdout, ok.stderr
    bad = subprocess.run([sys.executable, "-I", "-m", "app.cli", "run-jobs", "--only", "nonsense"], cwd=root, env=env, capture_output=True, text=True)  # noqa: S603
    assert bad.returncode != 0 and "Unknown job(s): nonsense" in (bad.stderr + bad.stdout)
