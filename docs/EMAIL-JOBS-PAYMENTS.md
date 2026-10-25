# Email, scheduled jobs and online payments: setting them up

These three go together: the jobs send email, and the payment links go in the email.

## 1. Real email

Until you set this up the office runs in **sandbox mode**: every message is written to the email log (Finance > Email log)
and nothing leaves the server. To send for real:

```bash
CLUB_EMAIL_PROVIDER=smtp
CLUB_SMTP_HOST=smtp.example.com
CLUB_SMTP_PORT=587                      # 465 uses SSL, anything else uses STARTTLS
CLUB_SMTP_USER=office@myc.example.org
CLUB_SMTP_PASSWORD_PATH=/etc/club/smtp-password   # a file readable only by the club user
CLUB_EMAIL_FROM="Margate Yacht Club <office@myc.example.org>"
CLUB_BASE_URL=https://members.myc.example.org
```

Use a transactional email service (for example Brevo, Mailgun, Postmark or Amazon SES) rather than a personal mailbox:
they handle bounces and keep you out of spam folders. Then set up the three DNS records on the club's domain, which
the service tells you how to create:

| Record | What it does |
| --- | --- |
| **SPF** (a `TXT` record) | Lists who may send mail as `myc.example.org`. |
| **DKIM** (a `TXT` or `CNAME`) | Signs each message so it cannot be forged. |
| **DMARC** (`_dmarc` `TXT`) | Tells inboxes what to do with mail that fails the two above. Start with `v=DMARC1; p=none; rua=mailto:office@myc.example.org` and tighten later. |

Test by sending an invoice to yourself and to a Gmail address, and look at "Show original": SPF, DKIM and DMARC
should all say PASS. A failed message is recorded as `failed`, retried by the nightly job up to three times, and then
left in the email log for a person (messages with a PDF attached cannot be rebuilt, so they wait for someone to press
"Email" on the invoice again).

Every mailing and renewal notice carries an unsubscribe link, and anyone who uses it is never mailed again.

## 2. Scheduled jobs

```bash
python -m app.cli run-jobs                    # whatever is due now
python -m app.cli run-jobs --dry-run          # list what it would do, change nothing
python -m app.cli run-jobs --only weekly_digests --force
```

| Job | When | What |
| --- | --- | --- |
| `overdue_reminders` | daily | Chases overdue invoices, at most every 14 days each, only where the invoice allows it |
| `renewal_notices` | daily | Once next season's renewal window has opened, tells members who have not renewed (monthly at most) |
| `insurance_reminders` | weekly | Asks boat owners with expired or unknown insurance to update it |
| `expire_training_holds` | daily | Releases course places nobody paid for |
| `qualification_expiry` | weekly | Warns people whose qualification runs out within 60 days |
| `weekly_digests` | weekly | Emails each hat-holder what needs doing in their area (nothing is sent when nothing needs doing) |
| `retry_emails` | daily | Tries failed messages again |
| `nightly_backup` | daily | Copies the database; keeps a fortnight |

The runner is safe to call as often as you like: a daily job runs once a day, a weekly one once a week. Run it every
hour from systemd:

`/etc/systemd/system/club-jobs.service`

```ini
[Unit]
Description=Club office scheduled jobs

[Service]
Type=oneshot
User=club
WorkingDirectory=/opt/club
EnvironmentFile=/etc/club.env
ExecStart=/opt/club/.venv/bin/python -m app.cli run-jobs
```

`/etc/systemd/system/club-jobs.timer`

```ini
[Unit]
Description=Run the club office jobs hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

Then `sudo systemctl enable --now club-jobs.timer`. The Admin screen *Jobs* shows what ran and lets you rehearse or
run any job by hand. **Rehearse the first time**, and check the lists look right before the first real run.

## 3. Online payments

Bank transfer always works and stays the default. The club saves most by steering members to **Direct Debit**
(GoCardless, roughly 1% plus 20p, capped; check current prices and charity rates), so it is offered first, with
**card** (Stripe) as the fallback. Nothing is switched on until you configure it:

```bash
CLUB_ONLINE_PAYMENTS=live
CLUB_GOCARDLESS_TOKEN=...                 # an access token from the GoCardless dashboard
CLUB_GOCARDLESS_ENV=sandbox               # use sandbox until you have tested; then remove this line
CLUB_GOCARDLESS_WEBHOOK_SECRET=...
CLUB_STRIPE_SECRET_KEY=sk_live_...
CLUB_STRIPE_WEBHOOK_SECRET=whsec_...
```

In each provider's dashboard point the webhook at the club's address:

* GoCardless: `https://members.myc.example.org/webhooks/gocardless`
* Stripe: `https://members.myc.example.org/webhooks/stripe`, event `checkout.session.completed`
  (and `checkout.session.expired`)

How it behaves: an invoice shows *Pay by Direct Debit* and *Pay by card* buttons to its owner; the payment links in
emails sign the member in first. **An invoice is marked paid only when the provider's signed notification reaches the
server**, never because a browser says it paid. Replayed or forged notifications are ignored. If a payment arrives for an
invoice that was already settled by bank transfer, it is not applied twice: it is flagged on the invoice for a refund.

**Before going live**, run one real payment through each provider's own *sandbox* with the settings above, using
`CLUB_GOCARDLESS_ENV=sandbox` and Stripe's test keys. The request shapes were written from the providers'
documentation and are covered by automated tests that fake the network, so the sandbox run is what proves them.

`CLUB_ONLINE_PAYMENTS=sandbox` is for **demos only**: it simulates a provider page so a committee can click through.
It is ignored (treated as off) unless the server is in development mode or `CLUB_DEMO=1`.
