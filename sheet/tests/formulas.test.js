/**
 * Calculates the register's spreadsheet formulas with a formula engine
 * (HyperFormula, which understands Google-style ARRAYFORMULA) and checks the
 * answers: invoice amounts, payments, balances and status, course places,
 * lookups and dropdown lists. Also checks the code's own sums agree with the
 * formulas, so the sheet and the Club menu never disagree about who owes what.
 *
 *   node tests/formulas.test.js
 */
'use strict';

const assert = require('assert');
const { HyperFormula } = require('hyperformula');
const { createEnv } = require('./fake-gas');

const env = createEnv({});
const TAB = env.run('TAB');
const SCHEMA = env.run('SCHEMA');
env.answer({ button: 'OK', text: 'Formula Test Club' });
env.run('setUpRegister()');
env.run('addExampleData()');
env.run('prepareMembershipInvoices()');
env.run('prepareBoatParkInvoices()');
env.quota = 100;
env.run('sendDraftInvoices()');
// Leave INV-1018 as a draft: make it a draft again by hand would break the rules, so make a new draft instead.
env.run('builderCreate({memberId: "M0001", type: "Other", lines: [{description: "Draft line", qty: 3, unit: 2.5}]})');

const today = env.run('today_()');
const pay = (no, amount, method) => {
  env.context.__p = { 'Date': today, 'Invoice no.': no, 'Amount £': amount, 'Method': method || 'Bank transfer' };
  env.run('appendRows_(table_(TAB.PAYMENTS), [__p])');
};
pay('INV-1001', 108);            // paid in full
pay('INV-1002', 50);             // part paid (165 due)
pay('INV-1003', 163);            // overpaid (153 due)
pay('INV-1005', 108);            // paid then refunded in part
pay('INV-1005', -8, 'Refund');   // so 8 is owed again
env.select(TAB.INVOICES, [5]);   // INV-1004 (no payments) is cancelled
env.run('cancelSelectedInvoice()');
env.context.__past = env.run('addDays_(today_(), -3)');
env.run('(function(){ const t = table_(TAB.INVOICES); t.rows.filter(r => r.get("Invoice no.") === "INV-1006")[0].set("Due", __past); })()');
env.select(TAB.BOOKINGS, [2]);
env.run('invoiceSelectedBookings()');

// ---------------------------------------------------------------------------
// Copy the fake spreadsheet into the formula engine
// ---------------------------------------------------------------------------

const ROWS = 300;
const serial = (d) => {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(d).reduce((o, p) => { o[p.type] = p.value; return o; }, {});
  return (Date.UTC(+parts.year, +parts.month - 1, +parts.day) - Date.UTC(1899, 11, 30)) / 86400000;
};
const hf = HyperFormula.buildEmpty({ licenseKey: 'gpl-v3', useArrayArithmetic: true, maxRows: ROWS });
const tabs = Object.keys(SCHEMA);
tabs.forEach((t) => hf.addSheet(t));
const formulas = [];
// Pass 1: every typed-in value on every tab. Pass 2: the formulas, so each one sees all the data.
tabs.forEach((t) => {
  const sheet = env.sheet(t);
  const width = SCHEMA[t].cols.length;
  const grid = [];
  for (let r = 1; r <= ROWS; r++) {
    const row = [];
    for (let c = 1; c <= width; c++) {
      const cell = r <= sheet.getLastRow() ? sheet.cell(r, c, false) : null;
      if (cell && cell.f) {
        // Translations for this engine only (the register keeps Google's own forms):
        //   open-ended A2:A                      → A2:A300
        //   VLOOKUP(x,{keys,values},2,FALSE)     → INDEX(values,MATCH(x,keys,0))  (same result; the engine
        //                                          cannot put ranges inside {…}, Google Sheets can)
        const f = cell.f.replace(/!([A-Z]+)2:\1(?![0-9])/g, '!$12:$1' + ROWS)
          .replace(/VLOOKUP\(([^,]+),\{([^,{}]+),([^{}]+)\},2,FALSE\)/g, 'INDEX($3,MATCH($1,$2,0))');
        formulas.push([t, r - 1, c - 1, f]);
        row.push(null);
      } else if (cell && cell.v instanceof Date) row.push(serial(cell.v));
      else row.push(cell && cell.v !== '' ? cell.v : null);
    }
    grid.push(row);
  }
  hf.setSheetContent(hf.getSheetId(t), grid);
});
// The engine needs a formula's sources in place first: invoice lines before invoices, invoices before bookings.
const order = (t) => (t === TAB.LINES ? 0 : t === TAB.BOOKINGS ? 2 : 1);
formulas.sort((a, b) => order(a[0]) - order(b[0]))
  .forEach(([t, r, c, f]) => hf.setCellContents({ sheet: hf.getSheetId(t), row: r, col: c }, [[f]]));

const computed = (t) => {
  const values = hf.getSheetValues(hf.getSheetId(t));
  const headers = SCHEMA[t].cols.map((c) => c.h);
  return values.slice(1).map((row) => {
    const o = {};
    headers.forEach((h, i) => { o[h] = row[i] === undefined ? null : row[i]; });
    return o;
  }).filter((o) => Object.values(o).some((v) => v !== null && v !== ''));
};
const errors = [];
tabs.forEach((t) => {
  hf.getSheetValues(hf.getSheetId(t)).forEach((row, i) => row.forEach((v, c) => {
    if (v && typeof v === 'object' && v.type) errors.push(t + ' row ' + (i + 1) + ' ' + SCHEMA[t].cols[c].h + ': ' + v.type + ' ' + (v.message || ''));
  }));
});

const results = [];
const check = (name, fn) => {
  try { fn(); results.push('PASS  ' + name); } catch (e) { results.push('FAIL  ' + name + '\n      ' + e.message.split('\n').slice(0, 6).join('\n      ')); }
};

check('No formula shows an error', () => assert.deepStrictEqual(errors, []));

const inv = computed(TAB.INVOICES);
const byNo = (no) => inv.find((r) => r['Invoice no.'] === no);

check('Line £ is Qty × Unit £', () => {
  const lines = computed(TAB.LINES);
  lines.forEach((l) => assert.strictEqual(l['Line £'], Math.round(l.Qty * l['Unit £'] * 100) / 100));
  const draft = lines.find((l) => l.Description === 'Draft line');
  assert.strictEqual(draft['Line £'], 7.5);
});

check('Amount, Paid, Balance and Status are worked out correctly', () => {
  const expect = {
    'INV-1001': [108, 108, 0, 'Paid'],
    'INV-1002': [165, 50, 115, 'Part paid'],
    'INV-1003': [153, 163, -10, 'Overpaid'],
    'INV-1004': [60, 0, 0, 'Cancelled'],
    'INV-1005': [108, 100, 8, 'Part paid'],
    'INV-1006': [108, 0, 108, 'Overdue'],
    'INV-1007': [120, 0, 120, 'Unpaid'],
  };
  Object.keys(expect).forEach((no) => {
    const r = byNo(no);
    assert.deepStrictEqual([r['Amount £'], r['Paid £'], r['Balance £'], r.Status], expect[no], no);
  });
  const draft = inv.find((r) => r.Description === 'Draft line');
  assert.deepStrictEqual([draft['Amount £'], draft.Status], [7.5, 'Draft']);
  const boat = byNo('INV-1019');
  assert.strictEqual(boat['Amount £'], 190, 'two boats at £95');
});

check('The code\'s sums agree with the spreadsheet\'s for every invoice', () => {
  const states = env.run('invoiceStates_().states');
  inv.forEach((r) => {
    const s = states[r['Invoice no.']];
    assert.ok(s, 'code knows ' + r['Invoice no.']);
    assert.strictEqual(s.amountPence, Math.round(r['Amount £'] * 100), r['Invoice no.'] + ' amount');
    assert.strictEqual(s.paidPence, Math.round(r['Paid £'] * 100), r['Invoice no.'] + ' paid');
    assert.strictEqual(s.balancePence, Math.round(r['Balance £'] * 100), r['Invoice no.'] + ' balance');
  });
});

check('Payments show who paid', () => {
  const p = computed(TAB.PAYMENTS);
  assert.strictEqual(p[0]['Bill to'], 'Alice Hughes');
  assert.strictEqual(p[1]['Bill to'], 'Ben Carter');
});

check('Members get a "Name (ID)" label for the Boats dropdown', () => {
  const m = computed(TAB.MEMBERS);
  assert.strictEqual(m[0]['Name and ID'], 'Alice Hughes (M0001)');
  const labels = m.map((x) => x['Name and ID']);
  computed(TAB.BOATS).filter((b) => b.Owner).forEach((b) => assert.ok(labels.indexOf(b.Owner) >= 0, b.Owner + ' is a valid choice'));
});

check('Fees feed the Membership and Boat park dropdowns', () => {
  const f = computed(TAB.FEES);
  const membership = f.map((x) => x['Membership codes']).filter(Boolean);
  const boatPark = f.map((x) => x['Boat park codes']).filter(Boolean);
  assert.strictEqual(membership.length, 16);
  assert.deepStrictEqual(boatPark, ['Boat park space', 'Kayak / SUP rack']);
});

check('Courses count Booked places and places left', () => {
  const c = computed(TAB.COURSES);
  assert.deepStrictEqual(c.map((x) => [x['Course ID'], x.Booked, x['Places left']]), [['C001', 0, 6], ['C002', 0, 6], ['C003', 2, 0]]);
});

check('Bookings show the course name and the payment status of their invoice', () => {
  const b = computed(TAB.BOOKINGS);
  assert.strictEqual(b[0].Course, 'RYA Powerboat Level 2');
  assert.strictEqual(b[0].Payment, 'Unpaid');
  assert.ok(b[1].Payment === '' || b[1].Payment === null, 'no invoice yet, so nothing shown');
});

results.forEach((r) => console.log(r));
const failed = results.filter((r) => r.startsWith('FAIL')).length;
console.log('\n' + (results.length - failed) + ' passed, ' + failed + ' failed.');
process.exit(failed ? 1 : 0);
