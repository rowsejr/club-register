# Club office: changes needed so the MYC website and Google Sheet can connect

For James. Repo: `rowsejr/club-register`. Written by Antony's Claude after reading the code at commit `e3a2e39`.
Nothing here changes how the office handles money. It is almost all feeds, settings and small switches.

**Status:** built on branch `website-integration`, merged to `main` and deployed. Each item below says what was
done. Run `GET /version` on the office to see the commit that is live. Section 2 is the agreed contract: if a feed
shape changes, update section 2 in the same commit.

## 0. How we will work

1. James makes the changes below on a branch called `website-integration` in VS Code, runs `pytest`, `ruff check .`, `mypy app` and `python tools/scan.py`, then pushes. CI must be green before merging to `main`.
2. Deploy to the Google Cloud server as now. Send Antony the commit hash.
3. Claude builds the website side (PHP, `test.margateyachtclub.co.uk`) and the Google Sheet script against the feed shapes below and tests them against a local copy of the office with demo data.
4. If a feed shape has to change later, update section 2 of this file in the same commit, so both sides stay in step.

## 1. Server settings (no code)

| # | Change | Done |
|---|---|---|
| 1.1 | `CLUB_FEED_KEYS="website:<32+ random chars>,sheet:<another 32+ random chars>"` | Yes. Two separate 48-character secrets on the server. James has them in a file on his PC to pass to Antony privately. Never in git. |
| 1.2 | Caddy: let `/feeds/*`, `/sso`, `/incoming/*`, `/static/*`, `/health`, `/readyz` and the public pages through without the Basic-auth prompt. | Yes. Also open: `/version`, `/healthz`, and the public pages `/`, `/membership`, `/join`, `/join-us`, `/whats-on` (and each event, its `.ics` and booking), `/events.ics`, `/courses`, `/privacy`, `/verify/*`, `/unsubscribe/*`, and the guest payment links `/i/*` and `/pay/*`. Everything else (sign-in, `/app`) still asks for the demo password. After `/sso` the member lands on `/app`, so on the demo the browser asks for the demo password once. |
| 1.3 | `CLUB_BASE_URL` and `CLUB_WEBSITE_URL` set to the real addresses. | The office is still at its temporary demo address (James has it) until `office.margateyachtclub.co.uk` is set up. `CLUB_WEBSITE_URL=https://test.margateyachtclub.co.uk`. |
| 1.4 | Email by SMTP through the club mailbox. | Not yet, as asked: waiting for Antony to fix delivery on the club domain. Email stays in test mode (written to the email log, nothing sent). |
| 1.5 | Keep `CLUB_ONLINE_PAYMENTS=off` and `CLUB_ENV=production` until launch. No real Stripe keys. | `CLUB_ENV=production`, no Stripe keys. Online payments are in **sandbox** mode, not off: a pretend payment page that cannot take money and switches itself off outside a demo. It lets the website's booking flow be tested end to end. Say if you want it off. |
| 1.6 | Confirm the live copy is at `e3a2e39` or later. | Yes: see `GET /version`. |

## 2. Code changes (the contract)

### 2.1 A version endpoint
`GET /version` returns `{"commit": "<short hash>", "built": "<date of that commit>"}`, no sign-in needed.

### 2.2 New feed: `siteMembers` (website; Sheet without Email and Mobile)
Every current member: active, or a membership that ended in the last 13 months, or joined in that time, or a new member
waiting to pay. Columns, in this order:

| Column | Notes |
|---|---|
| `Member id` | The office's own `members.id`. Never reused. The key the website keeps. |
| `Name` | Full name. |
| `Email` | The member's address, or the family address they share. May be empty. |
| `Mobile` | The main phone number on their record. May be empty. |
| `Membership` | Membership type name. |
| `Status` | `active`, `lapsed`, `resigned` or `pending` (accepted, waiting to pay). |
| `Until` | Expiry date, ISO, or empty. |
| `Household` | The lowest member id among people linked as family in the office or sharing an email address. The same for everyone in the household. |
| `Hats` | Office roles by name, comma and space between: `Admin` or `Committee` first, then `Treasurer`, `Secretary`, `Bosun`, `Social`, `Training`. Empty for an ordinary member. Roles belong to an email address, so everyone sharing that address shows them. |
| `Updated` | When the member or their contact record last changed, ISO with UTC offset. |

Not included: bank or invoice data, medical details, consents, date of birth, notes.

### 2.3 The `boats` feed (website and Sheet)
Every boat, one row per boat, including archived ones (see `Status`). Columns:
`Id`, `Name`, `Class`, `Sail number`, `Portsmouth number`, `Owner` (as before; `club` for club boats), then
`Boat id`, `Owner member id` (empty for club boats), `Club boat` (`yes`/`no`), `Status` (`active`, `archived`),
`Insurance until` (ISO), `Updated` (ISO with offset; empty for boats not changed since this version).
No space, berth or location columns.

### 2.4 Berths and dinghy park: optional
`CLUB_FEATURE_BERTHS=off` hides spaces, space allocation, seasons, the waiting list, storage tariffs and storage billing
(screens, menus, the Harbourmaster's to-do lines and members' "keep my space" and waiting-list buttons). The pages
answer "not found" while it is off. Default on. The data is kept. Boat park fees go on invoices as an ordinary item:
new databases get a "Boat park fee" item (price set by the Treasurer); on an existing one, add it on Money ▸ Prices.

### 2.5 Hats and money
See section 4, question 1.

### 2.6 Members edit their details in the office
Members could already change their mobile and other details on *My details*. They can now also change their email
address: they type the new one, the office emails a link to it (and tells the old address), and the address changes only
when they press the button on that link's page. Their old sign-ins end. People who hold a hat or a committee role
ask an administrator instead. Changes show in `siteMembers` straight away.

### 2.7 Events and courses
`events` feed: `Kind` is one of `social`, `racing`, `training`, `meeting`, `open_day`, `other` (older free-text kinds are
mapped to these), and a new last column `Website show` (`yes`/`no`), set by a tick box on each event, ticked by default.
Only published events appear in the feed at all.
Courses are not events in the office, so they have their own feed, `courses` (website and Sheet): `Course id`, `Title`,
`Starts`, `Ends`, `Where`, `Level`, `Price (£)`, `Places left`, `Minimum age`, `Book at`.

### 2.8 Sheet feeds
The Sheet may read `siteMembers` (without `Email` and `Mobile`) and `courses`. Google cannot hide one tab from people who
can open a spreadsheet, so money has its own spreadsheet: `sheet/Copy.gs` is set up as either the **committee** copy
(Members from `siteMembers`, Boats, Coming events, Courses, Bookings, RYA census) or the **treasurer** copy (Who owes,
Payments, Money summary, Members and money), shared only with the Treasurer.

### 2.9 Single sign-in from the website
`/sso` accepts `member_id`; the signed text is then `"sso\n<email>\n<member_id>\n<ts>\n<nonce>"`. The member must hold
that email or share it on their record. Without `member_id` it works as before. Staff and anyone with a hat are never
signed in through `/sso`, including through a family member who shares their address: they are sent to the office's
own sign-in. Both are covered by tests.

### 2.10 Deployment helper for cPanel
`deploy/cpanel/passenger_wsgi.py` (entry point `application`) and `requirements-cpanel.txt` (`-e .` and `a2wsgi`). A test
serves a request through it.

## 3. What the website will do with this (for reference)

- Hourly job reads `siteMembers`, `boats`, `events` and `courses` and replaces the website's copies.
- "My membership" and "Edit my details" buttons open the office through `/sso` (with `member_id`, and `next=/app/me/profile` for "Edit my details").
- Website forms (join enquiry, message for the Bosun) post to `/incoming/enquiry` and `/incoming/bosun`.
- Finance access on the website means "can open the office". The office decides what the person can then see.
- Nothing on the website writes to the office.

## 4. Questions for James: answers

1. **Hat names and who can see invoices.** Base roles: `Admin`, `Committee` (or an ordinary member). Hats: `Treasurer`,
   `Secretary` (membership), `Bosun` (boats), `Social` (events), `Training` (courses).
   - *See* invoices, debtors and the bank: Admin, every Committee member, and every hat.
   - *Change* money (invoices, payments, bank matching): Treasurer and Admin only. Void, write off, credit and fees:
     Treasurer and Admin. Closing the year: Treasurer and Admin.
   - Ordinary members see only their own invoices.
   So a "Finance" tick on the website would map to `Treasurer` or `Admin` in `Hats`. If seeing the books should be
   narrower than "everyone on the committee", that is a one-line change in `app/permissions.py`: say the word.
2. **Version and data.** The live copy is this branch's merge (see `/version`). It holds only made-up demo data.
3. **Feed shapes.** Built as written, with the notes in section 2 (Status values, how Household and Hats are worked out,
   courses as their own feed).
4. **Jobs and backups.** `club-jobs.timer` runs hourly on the server. (`/readyz` used to report a stopped timer in hours
   when no job was due; it now counts every hourly call, so it is safe to point an uptime monitor at `/readyz?strict=1`.) The nightly backup is made, checked and the last 14
   are kept on the server, but it is **not yet copied off the server**: that needs a storage bucket (a few pence a month)
   and `CLUB_BACKUP_COMMAND`. Worth doing before real data goes in.
