# Going live in January

A checklist for switching from the old system and sending the 2027 membership and boat park invoices.
Tick things off as you go.

## Before Christmas

**Accounts and the register**

- [ ] Make the club's own Google account (an ordinary Gmail address is fine). Turn on 2-step verification. Two
      officers keep the password and the backup codes.
- [ ] Signed in as the club account, install the register (README, "Install") and run **Set up the register**.
      Don't add example data to this one.
- [ ] Share it (**Share**, as Editor) with each role holder, and fill in the **Roles** tab with names and emails.
      Then **Club ▸ Admin ▸ Protect headers and settings**.

**Prices and settings**

- [ ] **Fees** tab: the committee agrees the 2027 prices for every membership type and boat park charge.
- [ ] **Settings** tab:
  - *Invoice prefix* and *First invoice number*: to carry on from the old system, set the number to one more than
    its last invoice.
  - *Club email*: where replies to invoices should go.
  - *Email footer*: the club's name and address.
  - *Privacy notice link*: shown on the forms.
  - *Membership year*: 2027.
  - Leave *Test mode* as **Yes** for now.

**Members and boats**

- [ ] Export members and boats from the old system as spreadsheets (CSV).
- [ ] Paste members onto the **Members** tab, matching the columns. Leave *Member ID* blank and it fills itself in.
      Every *Membership* must match a code on the Fees tab exactly; a red corner in the cell means it doesn't.
- [ ] Set *Status*: **Active** for everyone who should get a 2027 invoice; Lapsed or Left for the rest.
- [ ] Paste boats onto the **Boats** tab. For *Owner*, pick each owner from the dropdown (start typing their name).
      Set *Boat park fee* and *Status* (**In park** for every boat that should be charged).

**Practice run (on a copy)**

- [ ] **File ▸ Make a copy** of the register. Do the whole practice on the copy and delete it afterwards, so the
      real register's invoice numbers stay clean.
- [ ] In the copy: connect the Stripe **test** key, run both invoice runs and check the totals are what you expect
      from last year.
- [ ] Send a few (they all go to the test address), pay two with the test card `4242 4242 4242 4242`, add a pretend
      bank payment, and check the invoices turn green.
- [ ] Each role holder follows their part of the **Start here** tab once.

**Stripe**

- [ ] The Stripe account is in the club's name, with the club's bank account for payouts. Two officers can sign in.
- [ ] In Stripe's settings, turn on email receipts for successful payments, so members get a receipt from Stripe.
- [ ] Set the statement descriptor (what appears on members' bank statements) to the club's name.
- [ ] Make a **live** restricted key with the same permissions as the test one (README, "Card payments") and keep it
      ready for go-live day.

**Website and calendar**

- [ ] In the real register: **Club ▸ Admin ▸ Create the forms** and put the three links on the website.
- [ ] Enter the 2027 programme on the **Calendar** tab, **Publish the calendar**, make the Google Calendar public and
      put its embed code on the website.

## Going live (early January)

- [ ] **Club ▸ Admin ▸ Connect Stripe** with the **live** restricted key. Then **Turn on automatic payment checks**.
- [ ] **Settings ▸ Test mode: No.**
- [ ] **Day 1:** Club ▸ Membership ▸ Prepare membership invoices. Check the drafts, then **Send draft invoices**.
      Gmail sends about 100 a day; the rest wait.
- [ ] **Day 2:** **Send draft invoices** again to finish the memberships.
- [ ] **Day 3:** Club ▸ Boat park ▸ Prepare boat park invoices, check, and send.
- [ ] Keep the old system readable, if its licence allows, until the first payments are in.

## Then, through the year

- **Every week**, the Treasurer:
  - looks at the Invoices tab; a filter on Status shows what's owed
  - types in any bank payments
  - checks the Payments tab for card payments flagged *Not matched*
- **After the due date** (30 days), use **Send reminders**. It can be run every week or two; it never reminds the same
  person more than once in 14 days.
- **Once a month**, use **File ▸ Download ▸ Microsoft Excel** and keep the copy somewhere safe, as an extra backup.
