# Tests

The register runs inside Google, so these tests run it against simulated Google services instead (`fake-gas.js`):
spreadsheet tabs, Gmail with its daily limit, Forms, Calendar, triggers and a fake Stripe.

| Test | What it checks |
|---|---|
| `run-tests.js` | The whole year end to end: set-up, both invoice runs, sending within Gmail's limit, card and bank payments, reminders, cancelling, the invoice builder, forms, applications, bookings, calendar, protection, error messages. Every Stripe request is checked against Stripe's own API description. |
| `formulas.test.js` | Calculates the spreadsheet formulas with a formula engine and checks amounts, payments, balances, statuses, course places and lookups, and that the code's sums match the formulas. |
| `builder.test.js` | Opens the New invoice sidebar in a real browser, uses it, checks what it sends, and saves screenshots. |

To check Stripe requests, download Stripe's API description once:

```bash
curl -L -o tests/spec3.json https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json
```

Without it, that one check is skipped.

What the simulation can't prove: how the real Google screens look, the exact wording of Google's permission screens,
and Google's own limits on a busy day. The first run in a real Google account (on a practice copy) is the final test.
