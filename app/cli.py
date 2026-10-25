"""Command line for the person looking after the server: set up the database, back up and restore, run the timed jobs, make a demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select

from app import auth
from app.backup import backup, backup_dir, latest, redact, restore, restore_drill, verify
from app.db import DATABASE_URL, SessionLocal, init_db
from app.models import Role, UserRole


def _anonymise(out: str, seed: int | None) -> str:
    from app import anonymise as anon

    source = Path(DATABASE_URL.removeprefix("sqlite:///"))
    report = anon.anonymise(source, Path(out), seed)
    if not report.clean:
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(report.leaks.items()))
        raise SystemExit(f"Refused: real details survived in {detail}. The copy was deleted.")
    lines = [f"Wrote {out}", "Rows rewritten: " + ", ".join(f"{k} {v}" for k, v in sorted(report.tables.items()))]
    if report.warnings:
        lines.append("Check by eye (a real surname appears in free text): " + ", ".join(f"{k} x{v}" for k, v in sorted(report.warnings.items())))
    lines.append("Demo sign-ins: " + "; ".join(f"{email} ({role})" for email, role in report.accounts))
    return "\n".join(lines)


def _run_jobs(args: argparse.Namespace) -> str:
    from datetime import date

    from app import jobs

    init_db()
    day = date.fromisoformat(args.date) if args.date else None
    only = [n.strip() for n in args.only.split(",")] if args.only else None
    unknown = [n for n in (only or []) if n not in jobs.JOBS]
    if unknown:
        raise SystemExit(f"Unknown job(s): {', '.join(unknown)}. Jobs: {', '.join(jobs.JOBS)}")
    with SessionLocal() as db:
        runs = jobs.run_due(db, day, dry_run=args.dry_run, only=only, force=args.force, triggered_by="cli")
        lines = [f"{'(rehearsal) ' if r.dry_run else ''}{r.job}: {r.status} {json.dumps(r.summary, default=str)[:300]}" for r in runs]
    return "\n".join(lines) or "Nothing is due."


def main() -> None:
    parser = argparse.ArgumentParser(description="Club office command line")
    parser.add_argument("command", choices=["init-db", "backup", "restore", "verify-backup", "restore-drill", "create-admin", "anonymise", "issue-code", "run-jobs", "demo-data"])
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--file", dest="backup_file", help="Backup file for restore")
    parser.add_argument("--email", help="Email for create-admin or issue-code")
    parser.add_argument("--role", choices=["Admin", "Committee"], default="Admin")
    parser.add_argument("--yes", action="store_true", help="Confirm restore")
    parser.add_argument("--out", help="Where anonymise writes its copy (must not exist)")
    parser.add_argument("--seed", type=int, help="Make anonymise and demo-data repeatable")
    parser.add_argument("--dry-run", action="store_true", help="run-jobs: list what would be done and change nothing")
    parser.add_argument("--only", help="run-jobs: comma-separated job names to run now, whatever the schedule")
    parser.add_argument("--force", action="store_true", help="run-jobs: run every job even if it already ran today")
    parser.add_argument("--date", help="run-jobs: pretend today is this date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print(f"Database ready: {redact(DATABASE_URL)}")
        return
    if args.command == "create-admin":
        if not args.email:
            raise SystemExit("--email is required")
        init_db()
        with SessionLocal() as db:
            email = args.email.strip().lower()
            existing = db.scalar(select(UserRole).where(func.lower(UserRole.email) == email))
            if existing:
                existing.role = Role(args.role)
            else:
                db.add(UserRole(email=email, role=Role(args.role)))
            db.commit()
        print(f"{email} can now sign in as {args.role}")
        return
    if args.command == "demo-data":
        from app import fake_data

        init_db()
        with SessionLocal() as db:
            print(json.dumps(fake_data.build(db, seed=args.seed or 7)))
            db.commit()
        return
    if args.command == "run-jobs":
        print(_run_jobs(args))
        return
    if args.command == "issue-code":
        if not args.email:
            raise SystemExit("--email is required")
        init_db()
        with SessionLocal() as db:
            token = auth.issue_magic_link(db, args.email.strip().lower())
        print(f"Single-use sign-in code (valid 20 minutes): {token}" if token else "No account for that address")
        return
    if args.command == "anonymise":
        if not args.out:
            raise SystemExit("--out is required")
        print(_anonymise(args.out, args.seed))
        return
    if args.command == "backup":
        print(backup(DATABASE_URL, Path(args.backup_dir)))
        return
    if args.command in {"verify-backup", "restore-drill"}:
        chosen = Path(args.backup_file) if args.backup_file else latest(Path(args.backup_dir) if args.backup_dir != "backups" else backup_dir())
        if chosen is None:
            raise SystemExit("No backup found: pass --file or --backup-dir")
        report = verify(chosen) if args.command == "verify-backup" else restore_drill(DATABASE_URL, chosen)
        if args.command == "restore-drill":
            from app.models import AuditLog

            with SessionLocal() as db:
                db.add(AuditLog(actor_email="cli", actor_role="system", action="backup.restore_drill", target_type="backup", target_id=chosen.name, details={"ok": bool(report.get("ok") and report.get("restored")), "file": chosen.name}))
                db.commit()
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report.get("ok") and report.get("restored", True) else 1)
    if args.command == "restore":
        if not args.backup_file or not args.yes:
            raise SystemExit("restore requires --file <backup> and --yes (stop the app first)")
        print(f"Previous database saved to {restore(DATABASE_URL, Path(args.backup_file))}")
        return


if __name__ == "__main__":
    main()
