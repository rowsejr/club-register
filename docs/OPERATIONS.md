# Running the club office

What the person looking after the server needs to know.

## Settings (environment variables)

| Variable | What it does |
|---|---|
| `CLUB_ENV` | `production` on the live site. Turns on HSTS and removes the API docs. |
| `DATABASE_URL` | `sqlite:////var/lib/club/club.db` (see [SQLITE-DECISION.md](SQLITE-DECISION.md)). |
| `CLUB_BASE_URL` | The public address, `https://...`. Used in emails and pay links. |
| `CLUB_TRUST_PROXY=1` | Behind Caddy or nginx: read the real visitor address from the proxy. |
| `CLUB_EMAIL_PROVIDER` | `smtp` for real email. The default, `sandbox`, sends nothing. |
| `CLUB_ONLINE_PAYMENTS` | `live` to take GoCardless and Stripe payments (needs their keys). `sandbox` is for demos only. |
| `CLUB_BACKUP_DIR` | Where nightly backups are written (default `backups/`). |
| `CLUB_BACKUP_COMMAND` | Copies each backup off the server, for example `gsutil cp {file} gs://myc-office-backups/` or `rclone copy {file} remote:club`. `{file}` is replaced with the backup's path. No shell is used. |
| `CLUB_DEMO=1` | Demo site only: shows sign-in codes on the page. Never set it on the live site. |

## Backups

* A timer runs `python -m app.cli run-jobs` every hour. The job **nightly_backup** makes one backup a day, checks it opens and passes SQLite's
  integrity test, copies it off the server with `CLUB_BACKUP_COMMAND`, and keeps the latest fourteen. If any step fails the job shows as
  failed on **Admin > Jobs** and `/readyz` reports it.
* A backup that has never been restored is a hope, not a backup. **Once a month** run the drill, which restores a copy somewhere safe, opens
  it and compares it with the live data without touching anything live:

      python -m app.cli verify-backup            # the newest backup opens and passes its check
      python -m app.cli restore-drill            # the same, plus a comparison with the live database

  Each successful drill is recorded in the audit log.
* To restore for real: stop the app, `python -m app.cli restore --file backups/club-YYYYMMDD-HHMMSS.db --yes`, start the app. The current database
  is kept beside it as `*.pre-restore-*.db`.

## Watching the site

* `/healthz` answers `{"status": "ok"}` while the program is running. Use it for a simple "is it up" monitor.
* `/readyz?strict=1` also checks the database, that the hourly jobs ran in the last 3 hours and that a backup succeeded in the last 36. It returns
  503 with the reason when any of those fail, so a free monitor (UptimeRobot, Google Cloud Monitoring uptime check, Better Stack) can email the
  person on call. Leave off `strict=1` for a page that always answers 200 and shows the reasons in its text.
* Errors are logged with a reference (`journalctl -u club`). A visitor who sees "Something went wrong" is shown the same reference.

## Closing a year

**Finance > Year end** (treasurer): check nothing is left unmatched, download the examiner's pack, then close the books. Once closed, nothing
dated on or before the lock date can be issued, paid, voided or recategorised; mistakes are corrected with a credit note dated today. Only an
administrator can reopen, and must give a reason. Every lock and unlock is in the audit log.

## The audit log

**Admin > Audit** shows who did what and when, with the old and new values for edits to people, events and settings. It can be filtered by
person, action, record and date.

## Security built in

* Every page is sent with a strict content security policy (scripts only from this site), no framing, no sniffing, a referrer policy, and, on the
  live site, HSTS. Pages that hold people's details are marked `no-store`.
* Sign-in is by emailed code; sessions are signed and expire; every change needs a CSRF token; public forms have a honeypot, a time check and
  rate limits.
* Payments are marked paid only when the provider's signed notification says so.

## Accessibility

Every page has a language, a title, one main heading, a main landmark and a "skip to the content" link; every form field has a label; focus is
always visible; animations are turned off for people who ask for that. `tests/test_hardening.py` checks these on the main screens.
