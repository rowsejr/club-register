# Email, scheduled jobs and online payments: setting them up

These three go together: the jobs send email, and the payment links go in the email. Everything below goes in
`/etc/club.env` on the server (see [.env.example](../.env.example)).

## 1. Email from the club's own address

Until you set this up the office runs in **sandbox mode**. Every message is written to the email log
(Money ▸ Settings ▸ Email log) and nothing leaves the server.

The office sends through the **website host's mailbox**: make one mailbox, for example `office@margateyachtclub.co.uk`,
in the host's control panel (cPanel ▸ Email Accounts), then:

```bash
CLUB_EMAIL_PROVIDER=smtp
CLUB_SMTP_HOST=mail.margateyachtclub.co.uk     # the host's "outgoing server", shown next to the mailbox in cPanel
CLUB_SMTP_PORT=465                             # 465 uses SSL; 587 uses STARTTLS
CLUB_SMTP_USER=office@margateyachtclub.co.uk
CLUB_SMTP_PASSWORD_PATH=/etc/club/smtp-password   # a file holding the mailbox password, readable only by the club user
CLUB_EMAIL_FROM="Margate Yacht Club <office@margateyachtclub.co.uk>"
CLUB_BASE_URL=https://office.margateyachtclub.co.uk
```

**Replies go to the right volunteer.** Everything is sent from `office@`, but Money ▸ Settings has a reply address for
each subject:

| Subject | Emails | Suggested reply address |
| --- | --- | --- |
| Membership | enquiry replies, applications, renewal notices, members' requests | `membership@` |
| Invoices | invoices, reminders, receipts | `treasurer@` |
| Events and courses | tickets, waiting list, course bookings | `events@` |
| Boats | insurance reminders, boat park requests | `bosun@` |

A blank one falls back to the general reply address, and a blank general address means replies go to `office@`.
The addresses can be forwarders in cPanel; they do not need to be mailboxes.

**Check how many emails the host allows an hour** (often 100 to 500). A renewal run to every member goes out in one go,
so if the limit is low, ask the host to raise it for `office@`. Failed messages are retried by the nightly job.

**Stop it landing in spam: three DNS records.** If the domain's DNS is at Cloudflare, copy them from cPanel ▸ Email
Deliverability, which shows the exact values for this host:

| Record | What it does |
| --- | --- |
| **SPF** (a `TXT` record on the domain) | Lists the servers that may send mail as `margateyachtclub.co.uk`. Keep **one** SPF record only: merge, do not add a second. |
| **DKIM** (a `TXT` record such as `default._domainkey`) | Signs each message so it cannot be forged. |
| **DMARC** (a `TXT` record on `_dmarc`) | Tells inboxes what to do with mail that fails the two above. Start with `v=DMARC1; p=none; rua=mailto:office@margateyachtclub.co.uk` and tighten to `p=quarantine` once reports look clean. |

Test by sending an invoice to yourself and to a Gmail address, then use "Show original": SPF, DKIM and DMARC should all
say PASS. [mail-tester.com](https://www.mail-tester.com) should score 9/10 or better.

A failed message is recorded as `failed`, retried by the nightly job up to three times, then left in the email log for
a person. Messages with a PDF attached cannot be rebuilt, so they wait for someone to press "Email" on the invoice
again. Every mailing and renewal notice carries an unsubscribe link, and anyone who uses it is never mailed again.

## 2. Scheduled jobs

```bash
python -m app.cli run-jobs                    # whatever is due now
python -m app.cli run-jobs --dry-run          # list what it would do, change nothing
python -m app.cli run-jobs --only weekly_digests --force
```

| Job | When | What |
| --- | --- | --- |
| `direct_debit_collections` | daily | Collects invoices that are due from people who pay by Direct Debit. Each invoice is asked for once |
| `overdue_reminders` | daily | Chases overdue invoices, at most every 14 days each, only where the invoice allows it. Skips invoices a Direct Debit is collecting |
| `renewal_notices` | daily | Once next season's renewal window has opened, tells members who have not renewed (monthly at most) |
| `insurance_reminders` | weekly | Asks boat owners with expired or unknown insurance to update it |
| `expire_training_holds` | daily | Releases course places nobody paid for |
| `expire_event_holds` | daily | Releases ticket places nobody paid for, and offers them to the waiting list |
| `qualification_expiry` | weekly | Warns people whose qualification runs out within 60 days |
| `weekly_digests` | weekly | Emails each volunteer what needs doing in their area (nothing is sent when nothing needs doing) |
| `retry_emails` | daily | Tries failed messages again |
| `nightly_backup` | daily | Copies the database, checks the copy, keeps a fortnight, and sends one off the server |

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

Then `sudo systemctl enable --now club-jobs.timer`. Admin ▸ Jobs shows what ran and lets you rehearse or run any job by
hand. **Rehearse the first time**, and check the lists look right before the first real run.

## 3. Online payments: Stripe for card and Direct Debit

Bank transfer always works. On top of that, one Stripe account takes both:

- **Direct Debit (Bacs)**, offered first because it costs the club least. A member sets it up once, on Stripe's own
  page, and after that every invoice (the renewal, the boat park) is collected on its due date. Stripe emails them
  before each collection, as the Direct Debit rules require, and they can stop it from *My membership* at any time.
- **Card**, paid at once. Best for tickets and one-off payments.

Roughly what the club pays (check [Stripe's UK price list](https://stripe.com/gb/pricing) for today's figures, and ask
Stripe about charity pricing): a UK card costs about 1.5% plus 20p; Bacs Direct Debit is about 1% with a 20p minimum and
a cap of a few pounds. On a £165 family membership that is about £2.68 by card against about £1.65 by Direct Debit, and
the gap grows on bigger invoices. Stripe takes its fee and pays the rest into the club's bank account in one payout every
few days; the office records each payment against its invoice as "stripe".

The office collects invoices itself rather than using Stripe's subscriptions, which add a further fee.

### Switching it on

1. In the Stripe dashboard, under **Settings ▸ Payment methods**, turn on **Bacs Direct Debit** (Stripe checks the
   club's details first, which can take a few days) and make sure **Cards** is on.
2. Under **Developers ▸ API keys**, make a **restricted key** if you can (or use the secret key), and put it in
   the settings:

   ```bash
   CLUB_ONLINE_PAYMENTS=live
   CLUB_STRIPE_SECRET_KEY=sk_live_...        # sk_test_... while testing
   CLUB_STRIPE_WEBHOOK_SECRET=whsec_...
   # CLUB_DIRECT_DEBIT=stripe               # the default; "off" offers card only
   ```

3. Under **Developers ▸ Webhooks**, add an endpoint `https://office.margateyachtclub.co.uk/webhooks/stripe` with these
   events, and copy its signing secret into `CLUB_STRIPE_WEBHOOK_SECRET`:

   | Event | Why |
   | --- | --- |
   | `checkout.session.completed` | a card payment went through, or a Direct Debit was set up |
   | `checkout.session.expired` | someone gave up on the payment page |
   | `payment_intent.succeeded` | a Direct Debit collection was paid by the bank |
   | `payment_intent.payment_failed` | a Direct Debit collection failed (for example, not enough money) |
   | `mandate.updated` | a member cancelled their Direct Debit at their bank |
   | `charge.refunded` | someone refunded a payment in the Stripe dashboard |

4. Money ▸ Settings ▸ Direct Debit: choose whether due invoices are collected automatically every night (the default)
   or only when the member presses Pay.

### How it behaves

- **An invoice is marked paid only when Stripe's signed notification reaches the office**, never because a browser
  says it paid. Notifications that are forged, old or repeated are ignored.
- A Direct Debit takes a few working days. Meanwhile the invoice shows on the chase list as "being collected by Direct
  Debit" and nobody is sent a reminder.
- **A collection that fails is not tried again by itself.** The person goes back to normal chasing, and the reason
  shows on the invoice. They can press Pay to try again, or set up a new Direct Debit.
- **Refunds**: a refund made in the Stripe dashboard is taken off the invoice, so it shows as owed again and the books
  match the bank. If the club meant to give the money back (a cancelled event, say), add a credit note so nobody is
  chased. Bacs indemnity claims (a member asks their bank for money back) appear in Stripe as disputes; record them by
  hand as a reversed payment.
- If a payment arrives for an invoice already settled another way (a bank transfer got there first), it is not counted
  twice: it is flagged on the invoice for a refund.
- Non-members who buy a ticket get a private payment link; they never need an account.

### Before going live

Run it end to end with Stripe's **test keys**, using the Stripe CLI to pass notifications to your machine:

```bash
stripe listen --forward-to http://localhost:8000/webhooks/stripe   # prints the whsec_ secret to use while testing
```

1. Pay an invoice with the test card `4242 4242 4242 4242`. It should show as paid within seconds.
2. Set up a Direct Debit with Stripe's Bacs test details (sort code `10-88-00`, account number `00012345`), then press
   Pay on an invoice. It should show "being collected", then paid when Stripe's test clock catches up.
3. Use the test account number Stripe documents for a failed payment, and check the failure shows on the invoice.
4. In the dashboard, refund the test card payment and check the invoice shows as owed again.
5. Resend a notification from the dashboard (Webhooks ▸ the event ▸ Resend) and check nothing is counted twice.

The request shapes follow Stripe's documentation and are covered by automated tests that fake the network, so this test
run is what proves them against Stripe itself.

### GoCardless instead (optional)

GoCardless can take Direct Debits in place of Stripe, with Stripe kept for cards:

```bash
CLUB_DIRECT_DEBIT=gocardless
CLUB_GOCARDLESS_TOKEN=...
CLUB_GOCARDLESS_ENV=sandbox               # remove once tested
CLUB_GOCARDLESS_WEBHOOK_SECRET=...
```

with a webhook to `https://office.margateyachtclub.co.uk/webhooks/gocardless`. With GoCardless, members set up their
Direct Debit the first time they press Pay on an invoice.

### Demos

`CLUB_ONLINE_PAYMENTS=sandbox` is for **demos only**: it shows a pretend payment page so the committee can click
through. It is ignored (treated as off) unless the server is in development mode or `CLUB_DEMO=1`.
