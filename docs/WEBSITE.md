# The club website and the club office

The **website** (PHP, run by Antony) is the public face: pages, news, the calendar, results, the race box and duties.
The **club office** (this project, at `office.margateyachtclub.co.uk`) holds the records: members and households, boats,
invoices and payments, and bookable events and courses. Each does its own job, and they talk in a few small, signed ways.

| What | Which way | How |
|---|---|---|
| Members' directory, boats, coming events | office → website | The website reads a signed JSON feed every hour and keeps a copy in its own tables |
| "My membership" button | website → office | A signed link that opens the office already signed in (ordinary members only) |
| A question about joining, a message for the Bosun | website → office | The website's form posts the details to the office, signed |
| Join, book a ticket, book a course | link | Buttons on the website open the office's own pages, which use the same look |
| Calendar | office → anyone | `https://office.margateyachtclub.co.uk/events.ics`, and one `.ics` per event |

Nothing on the website can change money or membership records directly, and the office never reaches into the
website's database.

## 1. The key

The office administrator makes one long random secret for the website and sets it on the office server:

```bash
CLUB_FEED_KEYS="website:<at least 32 random characters>,sheet:<another one, for the committee's Sheet>"
```

The same secret goes in the website's configuration (not in its code or its git repository). Every request below is
signed with it using HMAC-SHA256: the parts are joined with a new line (`\n`), and the signature is lower-case hex.
Each request carries the time (Unix seconds) and a random `nonce`; the office refuses anything older than the limit or
any nonce it has already seen, so a copied request cannot be used again. Keep the website server's clock right (NTP).

PHP helper used in the examples:

```php
function club_sign(string $secret, array $parts): string {
    return hash_hmac('sha256', implode("\n", $parts), $secret);
}
function club_nonce(): string {
    return bin2hex(random_bytes(16));
}
```

## 2. Reading lists from the office (every hour)

```
GET https://office.margateyachtclub.co.uk/feeds/<name>.json?client=website&ts=<ts>&nonce=<nonce>&sig=<sig>
sig = HMAC(secret, "<name>\nwebsite\n<ts>\n<nonce>")          (refused after 5 minutes)
```

| Feed | Columns |
|---|---|
| `directory` | Name, Membership, Email, Phone. **Only members who agreed** to be in the members' directory |
| `boats` | Id, Name, Class, Sail number, Portsmouth number, Owner |
| `events` | Id, Title, Starts, Ends, Where, Kind, Member price (£), Visitor price (£), Places left, Book at |

Each answer is `{"feed", "title", "generated_at", "columns": [...], "rows": [[...], ...]}`. Times are ISO 8601 with the
UTC offset. "Book at" is the office's booking page for that event, empty when tickets are not on sale.

```php
function club_feed(string $name): array {
    $secret = getenv('CLUB_WEBSITE_KEY');
    $ts = (string) time();
    $nonce = club_nonce();
    $sig = club_sign($secret, [$name, 'website', $ts, $nonce]);
    $url = "https://office.margateyachtclub.co.uk/feeds/$name.json?" . http_build_query(
        ['client' => 'website', 'ts' => $ts, 'nonce' => $nonce, 'sig' => $sig]);
    $json = file_get_contents($url);            // or curl, with a 20 second timeout
    if ($json === false) { throw new RuntimeException("office feed $name failed"); }
    $data = json_decode($json, true);
    return array_map(fn($row) => array_combine($data['columns'], $row), $data['rows']);
}
// Run from cron once an hour; replace the website's copy of each table in one transaction.
```

**Members and boats on the website become read-only copies.** The race box and duties keep working from the
website's own tables; those tables are just refreshed from the office instead of being typed into. If the race box needs
a field that is not in a feed, ask and it will be added.

Show the events feed in the website's calendar with a **Book** button that opens `Book at`. A member who presses it
books at the member price after signing in; visitors book with their name and email and get a private payment link.

## 3. The "My membership" button: signing in from the website

The website already knows who is signed in. When a member presses **My membership**, make a fresh link and send them to
it straight away:

```
GET https://office.margateyachtclub.co.uk/sso?email=<email>&ts=<ts>&nonce=<nonce>&sig=<sig>
sig = HMAC(secret, "sso\n<email, trimmed and in lower case>\n<ts>\n<nonce>")     (works once, for 2 minutes)
```

```php
function club_office_link(string $email): string {
    $email = strtolower(trim($email));
    $ts = (string) time();
    $nonce = club_nonce();
    $sig = club_sign(getenv('CLUB_WEBSITE_KEY'), ['sso', $email, $ts, $nonce]);
    return 'https://office.margateyachtclub.co.uk/sso?' . http_build_query(
        ['email' => $email, 'ts' => $ts, 'nonce' => $nonce, 'sig' => $sig]);
}
// In the page handler for the button: header('Location: ' . club_office_link($user['email'])); exit;
```

Only make the link for someone the website has signed in, whose email address the website has confirmed.

What the office does:

- An **ordinary member** lands on their *My membership* page, signed in for 12 hours.
- **Anyone who helps run the club** (administrators, committee members, volunteers with a hat) is sent to the office's
  own sign-in page and gets a code by email. So even if the website were broken into, nobody could use it to open the
  club's records.
- An address the office does not know gets a polite page explaining who to ask.

## 4. Forms on the website that go to the office

```
POST https://office.margateyachtclub.co.uk/incoming/<kind>
Content-Type: application/json
X-Club-Client: website
X-Club-Timestamp: <ts>
X-Club-Nonce: <nonce>
X-Club-Signature: HMAC(secret, "<kind>\nwebsite\n<ts>\n<nonce>\n<SHA-256 hex of the exact body>")
```

| Kind | Body (JSON) | What happens |
|---|---|---|
| `enquiry` | `name`, `email`, `phone`, `interest` (`sailing`, `training`, `boat_storage`, `social` or `other`), `message`, `consent` | The person is emailed a link to confirm their address; then it is on the membership secretary's to-do list |
| `bosun` | `name`, `email`, `phone`, `message`, `consent` | From a member: goes straight to the Bosun's to-do list and inbox. From anyone else: an enquiry marked for the Bosun |

The answer is `{"ok": true, "message": "<thank-you to show the person>"}`, or `{"ok": false, "error": "..."}` with
status 422 for something the person should fix (such as a missing name), or 401/403 for a signing problem.

```php
function club_send_form(string $kind, array $fields): array {
    $body = json_encode($fields);
    $ts = (string) time();
    $nonce = club_nonce();
    $sig = club_sign(getenv('CLUB_WEBSITE_KEY'), [$kind, 'website', $ts, $nonce, hash('sha256', $body)]);
    $ch = curl_init("https://office.margateyachtclub.co.uk/incoming/$kind");
    curl_setopt_array($ch, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 20,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'X-Club-Client: website', "X-Club-Timestamp: $ts",
                               "X-Club-Nonce: $nonce", "X-Club-Signature: $sig"]]);
    $answer = json_decode((string) curl_exec($ch), true) ?: ['ok' => false, 'error' => 'The club office did not answer'];
    curl_close($ch);
    return $answer;
}
```

Keep the website's own spam protection (a hidden field and a minimum time on the page) in front of these forms: the
office trusts what the website sends. The office also limits how many forms it accepts an hour.

**Joining** is longer (household members, juniors' consent, the membership type, then payment), so the website's
**Join** button simply opens `https://office.margateyachtclub.co.uk/join`, which already has its own spam protection.

## 5. Checking your code: worked examples

With the secret `site-secret-0123456789`, `ts` = `1700000000` and `nonce` = `abcdef0123456789`:

| Request | Signature |
|---|---|
| Feed `events` | `7bda6d1a41c95113c7ca606682046da59756fbdba38a2f5fd87023baf513ad26` |
| Sign-in link for `ann@example.org` | `4577e481dd40bd12c72141eb17e624bfa0088ce721c0685ca9b33dcdb33f1f32` |
| Form `enquiry` with body `{"name":"Hannah Brook","email":"hannah@example.org"}` | `1737921d490ef34d3091c300d051372d97fb3185d090d94aa848e8e0c1d12170` |

The office's tests (`tests/test_website.py`, `tests/test_feeds.py`) check the same numbers.

## 6. Looking like one site

The office uses the website's font (Quattrocento Sans), its navy and red, and the club burgee in the header, with
large text and buttons. Pages a visitor reaches from the website (booking a ticket, joining, a course) have the same
header, and every office page links back to the website. If the website's colours change, they are set once at the
top of `app/static/club.css`.

## 7. Where the office runs

The office is one small Python program with one database file. Two options, both free or nearly so:

1. **The website's host**, if its control panel offers "Setup Python App" (Passenger). The office runs at
   `office.margateyachtclub.co.uk` beside the website. Check the host allows Python 3.12, a background job every hour
   (cron), and outgoing connections to Stripe.
2. **A small Google Cloud server** (the free e2-micro), reached through a **Cloudflare Tunnel** so it needs no public
   address and nothing is open to the internet except through Cloudflare. This is how the demo runs today.

Either way: the database is backed up every night, checked, and copied off the server
([OPERATIONS.md](OPERATIONS.md)); `/healthz` and `/readyz` are watched by a free uptime monitor; and email goes out
through the website host's mailbox ([EMAIL-JOBS-PAYMENTS.md](EMAIL-JOBS-PAYMENTS.md)).

## 8. To agree between the website and the office

1. Which fields the race box and duties need from the members and boats feeds.
2. Which events need booking (they get a **Book** button); the rest are calendar entries only.
3. Who looks after the keys, Cloudflare, the website host and Stripe, and who holds a spare copy of each.
4. Membership rules the office applies: the membership year, the renewal window, when unpaid renewals lapse, family
   limits and proposers.
