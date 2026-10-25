/**
 * Runs the register end to end against the fake Google and Stripe services:
 * set up, example data, both invoice runs, sending within Gmail's limit, card
 * and bank payments, reminders, cancelling, the invoice builder, forms,
 * applications, bookings, the calendar and header protection.
 *
 *   node tests/run-tests.js [--bundle] [path/to/stripe/spec3.json]
 *
 * --bundle tests dist/Club.gs (the file people paste in) instead of src/.
 */
'use strict';

const assert = require('assert');
const path = require('path');
const { createEnv, formatDate } = require('./fake-gas');

const args = process.argv.slice(2);
const bundle = args.indexOf('--bundle') >= 0 ? path.join(__dirname, '..', 'dist', 'Club.gs') : null;
const specPath = args.filter((a) => a !== '--bundle')[0] || process.env.STRIPE_SPEC || path.join(__dirname, 'spec3.json');
const env = createEnv({ stripeSpec: specPath, bundle });
console.log('Testing ' + (bundle ? 'dist/Club.gs' : 'src/') + (env.stripe.spec ? ', checking Stripe requests against spec3.json' : '') + '\n');
const TAB = env.run('TAB');
const results = [];

function step(name, fn) {
  try {
    fn();
    results.push(['PASS', name]);
  } catch (e) {
    results.push(['FAIL', name, e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : String(e)]);
  }
}
const rows = (tab) => env.rows(tab);
const invoice = (no) => rows(TAB.INVOICES).find((r) => r['Invoice no.'] === no);
const settingsValue = (name) => env.run('settings_()')[name];
const london = (d) => formatDate(d, 'Europe/London', 'yyyy-MM-dd HH:mm');
const setCell = (tab, rowNumber, header, value) => {
  env.context.__args = [tab, rowNumber, header, value];
  env.run('(function(a){ const t = table_(a[0]); t.rows.filter(r => r.n === a[1])[0].set(a[2], a[3]); })(__args)');
};
const noErrorDialog = () => {
  const bad = env.dialogs.filter((d) => /That did not work/.test(d.message));
  assert.deepStrictEqual(bad.map((d) => d.message), [], 'unexpected error dialog');
};

// ---------------------------------------------------------------------------
step('Menu is added when the spreadsheet opens', () => {
  env.run('onOpen()');
  const menu = env.menus[0];
  assert.strictEqual(menu.name, 'Club');
  const fns = [];
  const walk = (m) => m.items.forEach((i) => { if (i[0] === '>') walk(i[1]); else if (i[1]) fns.push(i[1]); });
  walk(menu);
  fns.forEach((fn) => assert.strictEqual(env.run('typeof ' + fn), 'function', 'menu item calls missing function ' + fn));
  assert.ok(fns.length >= 18);
});

step('Set up builds every tab, header, formula, setting and role', () => {
  env.answer({ button: 'OK', text: 'Test Sailing Club' });
  env.run('setUpRegister()');
  noErrorDialog();
  const names = env.ss.getSheets().map((s) => s.getName());
  assert.deepStrictEqual(names, [...env.run('TAB_ORDER')]);
  assert.strictEqual(env.ss.getSpreadsheetTimeZone(), 'Europe/London');
  const schema = env.run('SCHEMA');
  Object.keys(schema).forEach((tab) => {
    const headers = env.run('headerInfo_(SpreadsheetApp.getActiveSpreadsheet().getSheetByName(' + JSON.stringify(tab) + ')).headers');
    assert.deepStrictEqual([...headers], [...schema[tab].cols.map((c) => c.h)], 'headers of ' + tab);
    schema[tab].cols.forEach((c, i) => {
      const f = env.sheet(tab).getRange(1, i + 1).getFormulas()[0][0];
      if (c.f) assert.ok(f.startsWith('={"' + c.h + '";'), tab + ' ' + c.h + ' has its formula');
      else assert.strictEqual(f, '', tab + ' ' + c.h + ' has no formula');
    });
  });
  assert.strictEqual(settingsValue('Club name'), 'Test Sailing Club');
  assert.strictEqual(settingsValue('Test mode'), 'Yes');
  assert.strictEqual(settingsValue('Test email address'), 'admin@example.org');
  assert.ok(settingsValue('Membership year') >= 2026);
  const roles = rows(TAB.ROLES);
  assert.strictEqual(roles.length, 7);
  assert.strictEqual(roles[0].Email, 'admin@example.org');
  assert.ok(env.sheet(TAB.START).getLastRow() > 30, 'Start here is written');
});

step('Every formula points at the right columns', () => {
  // Rebuild each formula with column names instead of letters and compare with SCHEMA.
  const schema = env.run('SCHEMA');
  const headersOf = {};
  Object.keys(schema).forEach((tab) => { headersOf[tab] = schema[tab].cols.map((c) => c.h); });
  const letterIndex = (L) => L.split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  Object.keys(schema).forEach((tab) => {
    schema[tab].cols.forEach((c, i) => {
      if (!c.f) return;
      const actual = env.sheet(tab).getRange(1, i + 1).getFormulas()[0][0]
        .replace(/'([^']+)'!([A-Z]+)2:\2/g, (m, t, L) => '[' + t + '|' + headersOf[t][letterIndex(L)] + ']');
      const expected = '={"' + c.h + '";' + c.f({ L: (h) => '[' + tab + '|' + h + ']', R: (t, h) => '[' + t + '|' + h + ']' }) + '}';
      assert.strictEqual(actual, expected, tab + ' ' + c.h);
      let depth = 0;
      for (const ch of actual.replace(/"[^"]*"/g, '')) { if ('({'.includes(ch)) depth++; if (')}'.includes(ch)) depth--; assert.ok(depth >= 0); }
      assert.strictEqual(depth, 0, 'brackets balance in ' + tab + ' ' + c.h);
    });
  });
});

step('Header rows are protected; Settings, Roles and Log ask before edits', () => {
  const headerLocks = env.sheet(TAB.INVOICES).getProtections('RANGE');
  assert.strictEqual(headerLocks.length, 1);
  assert.strictEqual(headerLocks[0].range.row, 1);
  assert.strictEqual(headerLocks[0].isWarningOnly(), false);
  assert.ok(env.sheet(TAB.SETTINGS).getProtections('SHEET')[0].isWarningOnly());
  assert.strictEqual(env.sheet(TAB.MEMBERS).getProtections('SHEET').length, 0, 'data tabs stay freely editable');
});

step('Running set-up again changes nothing and adds no duplicates', () => {
  const before = env.ss.getSheets().map((s) => s.getName() + ':' + s.getLastColumn());
  env.run('setUpRegister()');
  noErrorDialog();
  assert.deepStrictEqual(env.ss.getSheets().map((s) => s.getName() + ':' + s.getLastColumn()), before);
  assert.strictEqual(rows(TAB.ROLES).length, 7);
  assert.strictEqual(env.sheet(TAB.INVOICES).getProtections('RANGE').length, 1, 'old protection replaced, not doubled');
});

step('Example data goes into an empty register', () => {
  env.run('addExampleData()');
  noErrorDialog();
  assert.strictEqual(rows(TAB.FEES).length, 20);
  assert.strictEqual(rows(TAB.MEMBERS).length, 22);
  assert.strictEqual(rows(TAB.BOATS).length, 12);
  assert.strictEqual(rows(TAB.COURSES).length, 3);
  assert.strictEqual(rows(TAB.CALENDAR).length, 7);
  env.run('addExampleData()');
  assert.match(env.lastDialog().message, /only go into an empty register/);
});

const year = () => Number(settingsValue('Membership year'));

step('Membership run: one draft per Active member, free and inactive left out, problems listed', () => {
  env.dialogs.length = 0;
  env.run('prepareMembershipInvoices()');
  noErrorDialog();
  const confirm = env.dialogs.find((d) => /^Make 18 draft membership invoices/.test(d.message));
  assert.ok(confirm, 'confirmation offered 18 invoices: ' + env.dialogs.map((d) => d.message).join(' || '));
  assert.match(confirm.message, /£1,983\.00/);
  assert.match(confirm.message, /Paul Murphy \(M0016\): no email address/);
  assert.match(confirm.message, /1 member\(s\) on free memberships/);
  const inv = rows(TAB.INVOICES);
  assert.strictEqual(inv.length, 18);
  assert.strictEqual(inv[0]['Invoice no.'], 'INV-1001');
  assert.strictEqual(inv[17]['Invoice no.'], 'INV-1018');
  assert.ok(inv.every((r) => r.Stage === 'Draft' && r.Type === 'Membership' && r.Year === year()));
  const lines = rows(TAB.LINES);
  assert.strictEqual(lines.length, 18);
  assert.strictEqual(lines[1].Description, 'Sailing family membership (2 adults, 2 children) ' + year());
  assert.strictEqual(lines[1]['Unit £'], 165);
  const days = Math.round((inv[0].Due - inv[0].Date) / 86400000);
  assert.strictEqual(days, 30, 'due in 30 days');
});

step('Membership run again: nothing to do (nobody billed twice)', () => {
  env.run('prepareMembershipInvoices()');
  assert.match(env.lastDialog().message, /Nothing to do/);
  assert.strictEqual(rows(TAB.INVOICES).length, 18);
});

step('Boat park run: one invoice per owner, all their boats on it, problems listed', () => {
  env.dialogs.length = 0;
  env.run('prepareBoatParkInvoices()');
  noErrorDialog();
  const confirm = env.dialogs.find((d) => /^Make 8 draft boat park invoices/.test(d.message));
  assert.ok(confirm, env.dialogs.map((d) => d.message).join(' || '));
  assert.match(confirm.message, /\(9 boats\), totalling £790\.00/);
  assert.match(confirm.message, /Mystery Topper.*no owner/);
  const ben = invoice('INV-1019');
  assert.strictEqual(ben['Bill to'], 'Ben Carter');
  assert.strictEqual(ben.Description, 'Boat park ' + year() + ' (2 boats)');
  const benLines = rows(TAB.LINES).filter((l) => l['Invoice no.'] === 'INV-1019');
  assert.deepStrictEqual(benLines.map((l) => l.Ref), ['B0001', 'B0002']);
  assert.match(benLines[0].Description, /Wild Goose \(Wayfarer 10234\), space C7/);
  env.run('prepareBoatParkInvoices()');
  assert.match(env.lastDialog().message, /Nothing to do/);
});

step('Sending stops at Gmail\'s daily limit and carries on the next day', () => {
  env.ss.active = env.sheet(TAB.MEMBERS);
  env.quota = 10;
  env.dialogs.length = 0;
  env.run('sendDraftInvoices()');
  noErrorDialog();
  assert.match(env.dialogs[0].message, /TEST MODE is on/);
  assert.match(env.dialogs[0].message, /sent from admin@example\.org/);
  assert.match(env.dialogs[0].message, /Stripe is not connected/);
  assert.match(env.dialogs[0].message, /send 8 more email/);
  assert.strictEqual(env.mail.length, 8);
  assert.match(env.lastDialog().message, /Emailed 8 invoice/);
  assert.match(env.lastDialog().message, /18 are still drafts/);
  const m = env.mail[0];
  assert.strictEqual(m.to, 'admin@example.org');
  assert.strictEqual(m.subject, '[TEST] Test Sailing Club invoice INV-1001: £108.00');
  assert.match(m.htmlBody, /would have gone to <b>alice\.hughes@example\.com<\/b>/);
  assert.doesNotMatch(m.htmlBody, /by card/);
  assert.strictEqual(m.attachments.length, 1);
  assert.strictEqual(m.attachments[0].type, 'application/pdf');
  assert.strictEqual(invoice('INV-1008').Stage, 'Sent');
  assert.strictEqual(invoice('INV-1009').Stage, 'Draft');
});

step('Connect Stripe (test key) and turn on the 15-minute check', () => {
  env.answer({ button: 'OK', text: 'rk_test_51abcDEF' });
  env.run('connectStripe()');
  noErrorDialog();
  assert.strictEqual(env.props.STRIPE_SECRET_KEY, 'rk_test_51abcDEF');
  assert.strictEqual(settingsValue('Stripe'), 'Connected, test mode (no real money)');
  env.run('turnOnPaymentChecks()');
  env.run('turnOnPaymentChecks()');
  const checks = env.triggers.filter((t) => t.fn === 'checkStripePayments');
  assert.strictEqual(checks.length, 1, 'only one trigger even if turned on twice');
  assert.strictEqual(checks[0].kind, 'every15min');
});

step('Sending the rest: each invoice gets its own Stripe pay link; no-email invoices are skipped', () => {
  env.quota = 100;
  env.run('sendDraftInvoices()');
  noErrorDialog();
  assert.match(env.lastDialog().message, /Emailed 16 invoice/);
  assert.match(env.lastDialog().message, /INV-1015 \(Paul Murphy\): no email address/);
  const isla = invoice('INV-1009');
  assert.strictEqual(isla.Stage, 'Sent');
  assert.match(isla['Pay link'], /^https:\/\/buy\.stripe\.com\/test_plink_\d+\?client_reference_id=INV-1009&prefilled_email=isla\.reid%40example\.com$/);
  assert.strictEqual(isla['Link £'], 108);
  const link = env.stripe.links[isla['Stripe link ID']];
  assert.strictEqual(link.params.line_items[0].price_data.unit_amount, '10800');
  assert.strictEqual(link.params.metadata.invoice_no, 'INV-1009');
  assert.strictEqual(link.params.restrictions.completed_sessions.limit, '1');
  const mail = env.mail.find((m) => /INV-1009/.test(m.subject));
  assert.match(mail.htmlBody, /Pay £108\.00 by card/);
  assert.ok(mail.htmlBody.includes(isla['Pay link'].replace(/&/g, '&amp;')));
  assert.strictEqual(invoice('INV-1015').Stage, 'Draft');
});

step('A card payment appears on the Payments tab, once, and its link is switched off', () => {
  const isla = invoice('INV-1009');
  env.stripe.pay(isla['Stripe link ID'], 'isla.reid@example.com');
  env.dialogs.length = 0;
  env.run('updatePaymentsNow()');
  noErrorDialog();
  assert.match(env.lastDialog().message, /INV-1009 £108\.00/);
  let pays = rows(TAB.PAYMENTS);
  assert.strictEqual(pays.length, 1);
  assert.strictEqual(pays[0]['Invoice no.'], 'INV-1009');
  assert.strictEqual(pays[0]['Amount £'], 108);
  assert.strictEqual(pays[0].Method, 'Card (Stripe)');
  assert.match(pays[0]['Stripe ID'], /^pi_test_/);
  assert.strictEqual(invoice('INV-1009')['Pay link'], '', 'pay link cleared once paid');
  env.run('checkStripePayments()');
  pays = rows(TAB.PAYMENTS);
  assert.strictEqual(pays.length, 1, 'the automatic check does not record it twice');
  const state = env.run('invoiceStates_().states["INV-1009"]');
  assert.strictEqual(state.balancePence, 0);
});

step('A payment that matches no invoice is recorded and flagged', () => {
  env.stripe.addEvent('checkout.session.completed', {
    id: 'cs_x', payment_status: 'paid', amount_total: 2500, currency: 'gbp', metadata: {}, payment_link: 'plink_elsewhere',
    payment_intent: 'pi_unknown', customer_details: { email: 'stranger@example.com' },
  });
  env.stripe.addEvent('checkout.session.completed', {
    id: 'cs_y', payment_status: 'unpaid', amount_total: 900, currency: 'gbp', metadata: { invoice_no: 'INV-1005' }, payment_intent: 'pi_later',
  });
  env.run('updatePaymentsNow()');
  const pays = rows(TAB.PAYMENTS);
  assert.strictEqual(pays.length, 2, 'unpaid (bank debit still pending) sessions are not recorded');
  assert.strictEqual(pays[1]['Invoice no.'], '');
  assert.match(pays[1].Notes, /Not matched to an invoice/);
  assert.match(env.lastDialog().message, /could not be matched/);
});

step('A bank part-payment, then reminders: new link for the balance, old link reused when unchanged', () => {
  env.context.__p = { 'Date': env.run('today_()'), 'Invoice no.': 'INV-1010', 'Amount £': 50, 'Method': 'Bank transfer', 'Reference': 'TURNER INV-1010' };
  env.run('appendRows_(table_(TAB.PAYMENTS), [__p])');
  const past = env.run('addDays_(today_(), -5)');
  setCell(TAB.INVOICES, invoice('INV-1010')._row, 'Due', past);
  setCell(TAB.INVOICES, invoice('INV-1011')._row, 'Due', past);
  const oldJack = invoice('INV-1010')['Stripe link ID'];
  const oldKate = invoice('INV-1011')['Stripe link ID'];
  const mailBefore = env.mail.length;
  env.dialogs.length = 0;
  env.run('sendReminders()');
  noErrorDialog();
  assert.match(env.dialogs[0].message, /Send reminders for 2 overdue invoice\(s\), £173\.50/);
  assert.strictEqual(env.mail.length, mailBefore + 2);
  const jack = invoice('INV-1010');
  assert.notStrictEqual(jack['Stripe link ID'], oldJack, 'new link for the new balance');
  assert.strictEqual(jack['Link £'], 109);
  assert.strictEqual(env.stripe.links[oldJack].active, false, 'old link switched off');
  assert.strictEqual(invoice('INV-1011')['Stripe link ID'], oldKate, 'unchanged amount keeps its link');
  assert.strictEqual(jack.Reminders, 1);
  const reminder = env.mail.find((m) => /Reminder: Test Sailing Club invoice INV-1010/.test(m.subject));
  assert.match(reminder.subject, /£109\.00 to pay/);
  assert.match(reminder.htmlBody, /Paid so far/);
  env.run('sendReminders()');
  assert.match(env.lastDialog().message, /Nobody needs a reminder/);
});

step('Cancelling: refused while there are payments, allowed otherwise; the run then re-bills', () => {
  env.select(TAB.INVOICES, [invoice('INV-1010')._row]);
  env.run('cancelSelectedInvoice()');
  assert.match(env.lastDialog().message, /has payments of £50\.00/);
  env.dialogs.length = 0;
  const liam = invoice('INV-1012');
  env.select(TAB.INVOICES, [liam._row]);
  env.run('cancelSelectedInvoice()');
  noErrorDialog();
  const after = invoice('INV-1012');
  assert.strictEqual(after.Stage, 'Cancelled');
  assert.strictEqual(after['Pay link'], '');
  assert.strictEqual(env.stripe.links[liam['Stripe link ID']].active, false);
  assert.match(after.Notes, /Cancelled .* by admin@example\.org/);
  env.ss.active = env.sheet(TAB.MEMBERS);
  env.run('prepareMembershipInvoices()');
  assert.strictEqual(invoice('INV-1027')['Bill to'], 'Liam Foster');
});

step('Invoice builder: data for the sidebar, checks, and an emailed invoice', () => {
  const data = env.run('builderData()');
  assert.strictEqual(data.members.length, 21, 'everyone except those who have Left');
  assert.strictEqual(data.members[0].name, 'Alice Hughes');
  assert.strictEqual(data.fees.length, 20);
  assert.strictEqual(data.testMode, true);
  assert.strictEqual(data.stripe, true);
  assert.throws(() => env.run('builderCreate({type: "Other", lines: []})'), /at least one line/);
  assert.throws(() => env.run('builderCreate({type: "Other", lines: [{description: "x", qty: 0, unit: 5}]})'), /Who is the invoice for/);
  assert.throws(() => env.run('builderCreate({memberId: "M0001", type: "Other", lines: [{description: "x", qty: 0, unit: 5}]})'), /whole number/);
  assert.throws(() => env.run('builderCreate({memberId: "M0001", type: "Other", lines: [{description: "x", qty: 1, unit: ""}]})'), /number of pounds/);
  const res = env.run('builderCreate({memberId: "M0001", type: "Other", year: 2027, send: true, lines: [{code: "Gate key deposit", description: "Gate key deposit", qty: 2, unit: "15"}]})');
  assert.strictEqual(res.invoiceNo, 'INV-1028');
  assert.strictEqual(res.total, '£30.00');
  assert.strictEqual(res.sent, true, res.message);
  assert.strictEqual(invoice('INV-1028').Stage, 'Sent');
  assert.strictEqual(invoice('INV-1028')['Link £'], 30);
  const draft = env.run('builderCreate({billTo: "Visiting Vic", email: "vic@example.com", type: "Training", lines: [{description: "Taster session", qty: 1, unit: 25}]})');
  assert.strictEqual(draft.sent, false);
  assert.strictEqual(invoice(draft.invoiceNo).Stage, 'Draft');
  assert.strictEqual(invoice(draft.invoiceNo)['Member ID'], '');
  env.run('openInvoiceBuilder()');
  assert.strictEqual(env.sidebars.length, 1);
  assert.strictEqual(env.sidebars[0].title, 'New invoice');
});

step('Forms are created, linked, and filled with choices', () => {
  env.dialogs.length = 0;
  env.run('createForms()');
  noErrorDialog();
  const forms = Object.values(env.forms);
  assert.strictEqual(forms.length, 3);
  const [join, booking, boat] = forms;
  assert.strictEqual(join.items.find((i) => i.title === 'Membership').choices.length, 14, 'priced memberships only');
  assert.strictEqual(join.items.find((i) => i.title === 'Membership').choices[0], 'Single Adult Sailing (£108.00)');
  const courses = booking.items.find((i) => i.title === 'Course').choices;
  assert.strictEqual(courses.length, 2, 'the full course is left off');
  assert.match(courses[0], /^C001: RYA Start Sailing \(Level 1\), 15 May \d{4} \(£150\.00\)$/);
  assert.strictEqual(boat.items.find((i) => i.title === 'What do you need?').choices.length, 2);
  assert.ok(env.sheet('Form: join') && env.sheet('Form: booking') && env.sheet('Form: boat'));
  const order = env.ss.getSheets().map((s) => s.getName());
  assert.strictEqual(order[0], TAB.START, 'form tabs are moved to the end');
  assert.deepStrictEqual(order.slice(-3), ['Form: join', 'Form: booking', 'Form: boat']);
  assert.strictEqual(settingsValue('Join form'), join.getPublishedUrl());
  assert.strictEqual(env.triggers.filter((t) => t.fn === 'onClubFormSubmit').length, 1);
});

const submit = (formIndex, answers) => {
  const form = Object.values(env.forms)[formIndex];
  const namedValues = {};
  Object.keys(answers).forEach((k) => { namedValues[k] = [answers[k]]; });
  env.context.__event = { range: { getSheet: () => form.destination }, namedValues };
  env.noUi = true; // triggers have no screen
  try { env.run('onClubFormSubmit(__event)'); } finally { env.noUi = false; }
};

step('A join form answer lands on Applications; anything formula-like is stored as plain text', () => {
  submit(0, { 'First name': 'Xena', 'Last name': '=IMPORTXML("https://evil.example","//a")', 'Email address': 'xena@example.com',
    'Phone': '+44 7700 900123', 'Membership': 'Non-Sailing Adult (£60.00)' });
  const apps = rows(TAB.APPLICATIONS);
  assert.strictEqual(apps.length, 3);
  assert.strictEqual(apps[2]['Membership wanted'], 'Non-Sailing Adult');
  assert.strictEqual(apps[2].Decision, 'New');
  assert.strictEqual(apps[2]['Last name'], '\'=IMPORTXML("https://evil.example","//a")');
  assert.strictEqual(apps[2].Phone, '\'+44 7700 900123');
  assert.strictEqual(env.sheet(TAB.APPLICATIONS).cell(4, 5).fmt, '@', 'phone column keeps leading zeros');
});

step('A booking with a place is invoiced and emailed; a full course goes on the waiting list', () => {
  const mailBefore = env.mail.length;
  submit(1, { 'Course': 'C001: RYA Start Sailing (Level 1), 15 May 2027 (£150.00)', 'Your name': 'Pat Jones', 'Email address': 'pat@example.com',
    'Phone': '07700 900111', 'Participant\'s name (if you are booking for someone else)': 'Sam Jones', 'Is the participant under 18?': 'Yes' });
  let b = rows(TAB.BOOKINGS);
  assert.strictEqual(b[3].Status, 'Booked');
  assert.strictEqual(b[3].Participant, 'Sam Jones');
  const no = b[3]['Invoice no.'];
  assert.ok(no, 'booking has an invoice');
  assert.strictEqual(invoice(no).Stage, 'Sent');
  assert.strictEqual(invoice(no).Type, 'Training');
  assert.ok(env.mail.length > mailBefore);
  submit(1, { 'Course': 'C003: RYA Powerboat Level 2, 3 July 2027 (£180.00)', 'Your name': 'Late Larry', 'Email address': 'larry@example.com', 'Phone': '1' });
  b = rows(TAB.BOOKINGS);
  assert.strictEqual(b[4].Status, 'Waiting list');
  assert.strictEqual(b[4]['Invoice no.'], '');
  assert.match(env.mail[env.mail.length - 1].subject, /Waiting list: RYA Powerboat Level 2/);
});

step('A boat park request from a member is matched to them', () => {
  submit(2, { 'Your name': 'Umar Ali', 'Email address': 'umar.ali@example.com', 'Boat name': 'Seal II', 'Class (e.g. Laser, Wayfarer, kayak)': 'Laser',
    'What do you need?': 'Boat park space (£95.00)' });
  const boats = rows(TAB.BOATS);
  const last = boats[boats.length - 1];
  assert.strictEqual(last.Owner, 'Umar Ali (M0020)');
  assert.strictEqual(last['Boat park fee'], 'Boat park space');
  assert.strictEqual(last.Status, 'Waiting for space');
  assert.strictEqual(last['Boat ID'], 'B0013');
});

step('Accepting an application: new member, first invoice emailed, Active once paid', () => {
  env.dialogs.length = 0;
  env.select(TAB.APPLICATIONS, [2]);
  env.run('acceptSelectedApplications()');
  noErrorDialog();
  const zoe = rows(TAB.MEMBERS).find((m) => m['First name'] === 'Zoe');
  assert.strictEqual(zoe['Member ID'], 'M0023');
  assert.strictEqual(zoe.Status, 'Applicant');
  assert.strictEqual(rows(TAB.APPLICATIONS)[0].Decision, 'Accepted');
  const inv = rows(TAB.INVOICES).find((r) => r['Member ID'] === 'M0023');
  assert.strictEqual(inv.Stage, 'Sent');
  env.stripe.pay(inv['Stripe link ID'], 'zoe.martin@example.com');
  env.run('updatePaymentsNow()');
  assert.match(env.lastDialog().message, /New members now Active:\n• Zoe Martin \(M0023\)/);
  const after = rows(TAB.MEMBERS).find((m) => m['First name'] === 'Zoe');
  assert.strictEqual(after.Status, 'Active');
  assert.ok(after.Joined instanceof Date);
});

step('Invoicing a booking by hand: refused when the course is full, done when there is a place', () => {
  env.select(TAB.BOOKINGS, [4]);
  env.run('invoiceSelectedBookings()');
  assert.match(env.lastDialog().message, /is full/);
  env.dialogs.length = 0;
  env.select(TAB.BOOKINGS, [2]);
  env.run('invoiceSelectedBookings()');
  noErrorDialog();
  assert.ok(rows(TAB.BOOKINGS)[0]['Invoice no.']);
});

step('Calendar: publish makes events at the right London times, then updates and removes them', () => {
  env.dialogs.length = 0;
  env.run('publishCalendar()');
  noErrorDialog();
  const cal = Object.values(env.calendars)[0];
  assert.strictEqual(cal.events.length, 7);
  const work = cal.events.find((e) => e.title === 'Fitting-out work party');
  assert.strictEqual(london(work.start), year() + '-03-06 10:00');
  assert.strictEqual(work.start.toISOString().slice(11, 16), '10:00', 'March is GMT');
  const course = cal.events.find((e) => /Start Sailing/.test(e.title));
  assert.strictEqual(course.start.toISOString().slice(11, 16), '08:30', 'May is BST (09:30 London)');
  const opens = cal.events.find((e) => e.title === 'Season opens');
  assert.ok(opens.allDay);
  assert.strictEqual(formatDate(opens.start, 'Europe/London', 'yyyy-MM-dd'), year() + '-04-03');
  assert.match(env.lastDialog().message, /7 added, 0 updated, 0 removed/);
  setCell(TAB.CALENDAR, 2, 'Event', 'Fitting-out work party (bring tools)');
  setCell(TAB.CALENDAR, 3, 'Status', 'Cancelled');
  env.run('publishCalendar()');
  assert.match(env.lastDialog().message, /0 added, 6 updated, 1 removed/);
  assert.strictEqual(cal.events.length, 6);
  assert.ok(cal.events.find((e) => e.title === 'Fitting-out work party (bring tools)'));
});

step('With test mode off, emails go to the real address', () => {
  const s = rows(TAB.SETTINGS).find((r) => r.Setting === 'Test mode');
  setCell(TAB.SETTINGS, s._row, 'Value', 'No');
  env.run('builderCreate({memberId: "M0003", type: "Other", send: true, lines: [{description: "Laying-up supper ticket", qty: 1, unit: 18}]})');
  const m = env.mail[env.mail.length - 1];
  assert.strictEqual(m.to, 'chloe.patel@example.com');
  assert.ok(!m.subject.startsWith('[TEST]'));
  setCell(TAB.SETTINGS, s._row, 'Value', 'Yes');
});

step('If Google cannot make the PDF, the email still goes (without it) and the log says so', () => {
  env.failPdf = true;
  const res = env.run('builderCreate({memberId: "M0005", type: "Other", send: true, lines: [{description: "Donation", qty: 1, unit: 10}]})');
  env.failPdf = false;
  assert.strictEqual(res.sent, true);
  const m = env.mail[env.mail.length - 1];
  assert.ok(!m.attachments);
  assert.ok(rows(TAB.LOG).some((r) => r.What === 'PDF not attached'));
});

step('Re-applying protection after an Admin is added; an Admin without access is reported', () => {
  setCell(TAB.ROLES, 3, 'Email', 'treasurer@example.org');
  setCell(TAB.ROLES, 3, 'Name', 'Second Admin');
  env.run('lockTabs()');
  const p = env.sheet(TAB.INVOICES).getProtections('RANGE');
  assert.strictEqual(p.length, 1);
  assert.ok(p[0].getEditors().some((u) => u.getEmail() === 'treasurer@example.org'));
  setCell(TAB.ROLES, 3, 'Email', 'stranger@example.org');
  env.run('lockTabs()');
  assert.match(env.lastDialog().message, /stranger@example\.org is listed as an Admin but cannot edit/);
});

step('Small helpers: money, times, IDs, dates across the clock change, Stripe encoding', () => {
  assert.strictEqual(env.run('money_(123456)'), '£1,234.56');
  assert.strictEqual(env.run('money_(-5)'), '-£0.05');
  assert.strictEqual(env.run('timeText_("9.30")'), '09:30');
  assert.strictEqual(env.run('timeText_("25:00")'), '');
  assert.strictEqual(env.run('idFromLabel_("Jane Smith (M0007)")'), 'M0007');
  const d = env.run('addDays_(Utilities.parseDate("2027-03-27", "Europe/London", "yyyy-MM-dd"), 1)');
  assert.strictEqual(formatDate(d, 'Europe/London', 'yyyy-MM-dd HH:mm'), '2027-03-28 00:00');
  assert.strictEqual(env.run('formEncode_({a: [{b: {c: 1}}], m: {k: "x y"}, skip: undefined}, "")'),
    'a%5B0%5D%5Bb%5D%5Bc%5D=1&m%5Bk%5D=x%20y');
  assert.strictEqual(env.run('pence_("12.345")'), 1235);
});

step('Guards: a sorted tab is never written to the wrong row, and a row typed meanwhile is not overwritten', () => {
  const swap = (tab, a, b) => {
    const s = env.sheet(tab);
    const w = s.getLastColumn();
    const ra = s.getRange(a, 1, 1, w).getValues();
    const rb = s.getRange(b, 1, 1, w).getValues();
    s.getRange(a, 1, 1, w).setValues(rb);
    s.getRange(b, 1, 1, w).setValues(ra);
  };
  env.context.__t = env.run('table_(TAB.INVOICES)');
  swap(TAB.INVOICES, 2, 3); // someone sorts the tab while a run is going
  assert.throws(() => env.run('__t.rows[0].set("Notes", "should not land on the wrong invoice")'), /changed while this was running/);
  swap(TAB.INVOICES, 2, 3);
  assert.ok(!rows(TAB.INVOICES).some((r) => /should not land/.test(r.Notes)));

  env.context.__pay = env.run('table_(TAB.PAYMENTS)');
  const next = env.context.__pay.lastRow + 1;
  env.sheet(TAB.PAYMENTS).getRange(next, 2, 1, 2).setValues([['INV-1001', 1]]); // the Treasurer types a payment meanwhile
  env.run('appendRows_(__pay, [{"Invoice no.": "INV-1002", "Amount £": 2, "Method": "Cash"}])');
  const p = rows(TAB.PAYMENTS);
  assert.strictEqual(p.find((r) => r._row === next)['Invoice no.'], 'INV-1001', 'the typed row is kept');
  assert.strictEqual(p.find((r) => r._row === next + 1)['Invoice no.'], 'INV-1002', 'the new row goes below it');
});

step('A problem shows a plain message and is written to the Log', () => {
  env.ss.sheets = env.ss.sheets.filter((s) => s.name !== TAB.COURSES);
  env.run('updateForms()');
  assert.match(env.lastDialog().message, /That did not work:\n\nThe "Courses" tab is missing/);
  assert.ok(rows(TAB.LOG).some((r) => r.What === 'Problem: Update the forms'));
});

if (env.stripe.spec) {
  step('Every Stripe request matched Stripe\'s published API', () => {
    assert.ok(env.stripe.requests.length > 20);
    assert.deepStrictEqual(env.stripe.problems, []);
  });
} else {
  results.push(['SKIP', 'Stripe requests not checked against Stripe\'s API: tests/spec3.json is missing (see tests/README.md)']);
}

// ---------------------------------------------------------------------------
const failed = results.filter((r) => r[0] === 'FAIL');
results.forEach((r) => console.log(r[0] + '  ' + r[1] + (r[2] ? '\n      ' + r[2] : '')));
console.log('\n' + (results.length - failed.length) + ' passed, ' + failed.length + ' failed. Emails sent: ' + env.mail.length +
  '. Stripe requests: ' + env.stripe.requests.length + '.');
process.exit(failed.length ? 1 : 0);
