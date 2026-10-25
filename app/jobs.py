"""Scheduled work: reminders, renewal notices, digests, retries and backups, run from one place.

``python -m app.cli run-jobs`` runs whatever is due; a systemd timer or cron calls it every hour. Every job

* knows how often it should run (so calling the runner twice in a row does nothing the second time),
* can be rehearsed with ``--dry-run``, which lists what it *would* do and changes nothing,
* is safe to repeat (it only acts on people who have not already been told or billed), and
* leaves a record in ``job_runs`` that the Admin screen shows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import boats, digests, events, finance, invoice_comms, mailer, membership, season, training
from app.models import JobRun, OutboundEmail, Qualification

ACTOR = "scheduler"


@dataclass(frozen=True)
class Job:
    name: str
    label: str
    schedule: str  # daily | weekly (Mondays)
    run: Callable[[Session, date, bool], dict]


# --------------------------------------------------------------------------- the jobs. Each returns a summary; dry_run changes nothing.


def overdue_reminders(db: Session, today: date, dry_run: bool) -> dict:
    """Chase invoices that are overdue, no more than once every 14 days each, and only where the invoice allows it."""
    candidates = [c for c in invoice_comms.reminder_candidates(db, today=today, min_days_overdue=1) if c.can_email and c.invoice.send_reminders is not False]
    if dry_run:
        return {"would_remind": [c.invoice.reference for c in candidates]}
    result = invoice_comms.send_reminders(db, [c.invoice.id for c in candidates], ACTOR)
    return {"sent": len(result["sent"]), "skipped": len(result["skipped"])}


def renewal_notices(db: Session, today: date, dry_run: bool) -> dict:
    """Once the renewal window for next season has opened, tell members who have not renewed (at most monthly each)."""
    year = today.year + 1
    if today < membership._window_opens(year, membership.renewal_window_months(db)):
        return {"skipped": f"the {year} renewal window has not opened"}
    waiting = [c for c in season.notice_candidates(db, year) if c.can_email and (c.last_notice is None or (today - c.last_notice).days >= season.NOTICE_GAP_DAYS)]
    if dry_run:
        return {"would_tell": [c.contact.name for c in waiting]}
    result = season.send_notices(db, year, ACTOR, member_ids=[c.member.id for c in waiting], today=today)
    return {"sent": len(result["sent"]), "skipped": len(result["skipped"])}


def insurance_reminders(db: Session, today: date, dry_run: bool) -> dict:
    problems = [boat for boat, _state in boats.insurance_problems(db, today)]
    if dry_run:
        return {"would_remind": [b.name for b in problems]}
    result = boats.send_insurance_reminders(db, [b.id for b in problems], ACTOR, today)
    return {"sent": len(result["sent"]), "skipped": len(result["skipped"])}


def expire_training_holds(db: Session, today: date, dry_run: bool) -> dict:
    if dry_run:
        return {"would_expire": "unpaid places held longer than the hold period"}
    return {"expired": training.expire_unpaid(db, datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12))}


def qualification_expiry(db: Session, today: date, dry_run: bool) -> dict:
    """Email people whose qualification runs out within 60 days, at most once a month each."""
    due = [e for e in training.expiring_qualifications(db, 60, today) if e.contact.email and not e.contact.suppressed and e.days_left >= -30]
    if dry_run:
        return {"would_tell": [f"{e.contact.name}: {e.qualification.name}" for e in due]}
    club, sent = finance.get_setting(db, "club_name"), 0
    for item in due:
        last = db.scalar(select(OutboundEmail.created_at).where(OutboundEmail.related_type == "qualification_expiry", OutboundEmail.related_id == item.qualification.id).order_by(OutboundEmail.id.desc()))
        if last is not None and (datetime.now(UTC) - (last if last.tzinfo else last.replace(tzinfo=UTC))).days < 30:
            continue
        q: Qualification = item.qualification
        when = q.expires_on.strftime("%d %B %Y") if q.expires_on else "soon"
        mailer.send_email(
            db, to=item.contact.email or "", subject=f"Your {q.name} runs out {when}",
            body=f"Hello {item.contact.name.split()[0]},\n\nOur records show your {q.name} {'has run out' if item.days_left < 0 else 'runs out'} on {when}. If you have renewed it, please let the training officer know so we can update your record.\n\n{club}",
            related_type="qualification_expiry", related_id=q.id, actor=ACTOR,
        )
        sent += 1
    return {"sent": sent}


def expire_event_holds(db: Session, today: date, dry_run: bool) -> dict:
    """Give back ticket places nobody paid for, and offer them to the waiting list."""
    if dry_run:
        return {"would_release": "unpaid ticket holds past their deadline"}
    return {"released": events.expire_holds(db, datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12))}


def weekly_digests(db: Session, today: date, dry_run: bool) -> dict:
    return digests.send_all(db, today, dry_run=dry_run, actor=ACTOR)


def retry_emails(db: Session, today: date, dry_run: bool) -> dict:
    return mailer.retry_failed(db, dry_run=dry_run)


def nightly_backup(db: Session, today: date, dry_run: bool) -> dict:
    from app import backup as backups
    from app.db import DATABASE_URL

    folder = backups.backup_dir()
    if dry_run:
        return {"would_back_up_to": str(folder)}
    snapshot = backups.backup(DATABASE_URL, folder)
    check = backups.verify(snapshot) if snapshot.suffix == ".db" else {"ok": True}
    if not check["ok"]:
        raise RuntimeError(f"The backup {snapshot.name} failed its check: {check.get('problem')}")
    kept = sorted(folder.glob("club-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in kept[14:]:  # keep the latest fortnight; this happens first so a broken off-server copy cannot fill the disk
        old.unlink(missing_ok=True)
        removed += 1
    copied = backups.offsite(snapshot)
    return {"backup": snapshot.name, "verified": True, "offsite": copied, "pruned": removed}


JOBS: dict[str, Job] = {
    j.name: j
    for j in (
        Job("overdue_reminders", "Remind people about overdue invoices", "daily", overdue_reminders),
        Job("renewal_notices", "Tell members it is time to renew", "daily", renewal_notices),
        Job("insurance_reminders", "Ask boat owners to update their insurance", "weekly", insurance_reminders),
        Job("expire_training_holds", "Release course places that were not paid for", "daily", expire_training_holds),
        Job("expire_event_holds", "Release unpaid event ticket holds", "daily", expire_event_holds),
        Job("qualification_expiry", "Warn people their qualification is running out", "weekly", qualification_expiry),
        Job("weekly_digests", "Email each volunteer role what needs doing", "weekly", weekly_digests),
        Job("retry_emails", "Try again with emails that failed to send", "daily", retry_emails),
        Job("nightly_backup", "Back up the database", "daily", nightly_backup),
    )
}


# --------------------------------------------------------------------------- the runner


def is_due(job: Job, today: date, last: date | None) -> bool:
    """Daily jobs run once per calendar day; weekly jobs once per week, on or after Monday (so a missed Monday runs on Tuesday)."""
    if last is None:
        return True  # never run: run it now rather than wait for a particular day
    if job.schedule == "weekly":
        return today.isocalendar()[:2] != last.isocalendar()[:2]
    return today > last


def last_real_run(db: Session, name: str) -> date | None:
    stamps = db.scalars(select(JobRun.run_date).where(JobRun.job == name, JobRun.dry_run.is_(False), JobRun.status == "ok").order_by(JobRun.run_date.desc())).first()
    return stamps


def run_job(db: Session, job: Job, today: date, dry_run: bool, triggered_by: str = "cron") -> JobRun:
    record = JobRun(job=job.name, dry_run=dry_run, run_date=today, triggered_by=triggered_by, status="running")
    db.add(record)
    db.flush()
    savepoint = db.begin_nested()
    try:
        record.summary = job.run(db, today, dry_run)
        savepoint.commit()
        record.status = "ok"
    except Exception as exc:  # noqa: BLE001 - one broken job must not stop the others
        savepoint.rollback()
        record.status, record.summary = "failed", {"error": str(exc)[:300]}
    record.finished_at = datetime.now(UTC)
    db.flush()
    return record


def run_due(db: Session, today: date | None = None, *, dry_run: bool = False, only: list[str] | None = None, force: bool = False, triggered_by: str = "cron") -> list[JobRun]:
    """Run every job that is due (or the named ones). A rehearsal never counts as the day's run."""
    today = today or date.today()
    runs = []
    for name, job in JOBS.items():
        if only and name not in only:
            continue
        if not (force or only) and not is_due(job, today, last_real_run(db, name)):
            continue
        runs.append(run_job(db, job, today, dry_run, triggered_by))
        db.commit()
    return runs


def recent_runs(db: Session, limit: int = 40) -> list[JobRun]:
    return list(db.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(limit)))
