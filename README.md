# Club register

The club office for Margate Yacht Club: membership, payments and bookable events, kept simple enough for any
volunteer to use. It sits beside the club website, which shows the public pages, the calendar, results and duties.

| Area | What it does |
|---|---|
| New members | Enquiries from the website get an automatic reply; applications are checked, accepted and invoiced; payment starts the membership |
| Members | Households, juniors and consents, membership numbers, renewals each season, checks that keep the records up to date |
| Boats | Boat register, insurance dates, dinghy park spaces, the waiting list and storage charges |
| Money | Invoices, payment status everywhere, a chase list for anyone who owes, card and Direct Debit through Stripe, bank payments, credit notes, reports and the year-end pack |
| Events | Tickets for members and visitors, waiting lists, courses, attendee lists |
| Running it | Roles ("hats") for each volunteer, scheduled jobs, backups, an audit log, health checks |

It is a FastAPI and SQLAlchemy application with server-rendered pages (Jinja2 and HTMX, no build step) and one SQLite
database. See [docs/SQLITE-DECISION.md](docs/SQLITE-DECISION.md).

## Run it on your own machine

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"          # .venv/bin/pip on Linux and macOS
cp .env.example .env
python -m app.cli init-db
python -m app.cli demo-data                     # a made-up club to explore (only on an empty database)
python -m app.cli create-admin --email you@example.org
uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in. With `CLUB_ENV=dev` the sign-in code is shown on screen instead of emailed.
The made-up club has sign-ins for `admin@`, `treasurer@`, `secretary@`, `bosun@`, `training@` and `social@example.org`.

## Checks

```bash
pytest
ruff check .                 # do not run "ruff format": the code is laid out by hand
mypy app
python tools/scan.py         # no forbidden words and no personal data anywhere in the repository
node --test "sheet/tests/*.test.js"
```

Install the scan as a pre-commit hook with `git config core.hooksPath tools/hooks`.

## Documents

- [docs/HAT-GUIDES.md](docs/HAT-GUIDES.md): one page per volunteer role
- [docs/OPERATIONS.md](docs/OPERATIONS.md): settings, backups, monitoring, closing a year
- [docs/EMAIL-JOBS-PAYMENTS.md](docs/EMAIL-JOBS-PAYMENTS.md): email from the club's address, scheduled jobs, Stripe card and Direct Debit
- [docs/WEBSITE.md](docs/WEBSITE.md): how the club website and the office work together
- [docs/SQLITE-DECISION.md](docs/SQLITE-DECISION.md): why one SQLite file

[sheet/](sheet/README.md) is a short Google Apps Script that keeps a read-only copy of key lists (who owes, payments,
members without contact details, bookings, the RYA census) in a Google Sheet for the committee.
