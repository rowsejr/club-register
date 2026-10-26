// Run with: node --test sheet/tests
// Checks the Sheet signs requests exactly as the office expects (the same example is in tests/test_feeds.py),
// and that a feed lands in a protected tab.
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const VECTOR = { secret: 'test-secret-0123456789', name: 'members', client: 'sheet', ts: '1700000000', nonce: 'abcdef0123456789',
  sig: '9257ecfd5a118024972c84a9bd6fb6d2a8da02e07c802d328903b37ca6f2f3a6' };

function load(extra) {
  const sandbox = {
    module: { exports: {} },
    Utilities: {
      // Like Google's: a list of signed bytes (-128 to 127).
      computeHmacSha256Signature: (value, key) => Array.from(crypto.createHmac('sha256', key).update(value).digest()).map((b) => (b > 127 ? b - 256 : b)),
      getUuid: () => crypto.randomUUID()
    },
    ...extra
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'Copy.gs'), 'utf8'), sandbox);
  return sandbox.module.exports;
}

test('signs exactly as the office does', () => {
  const sheet = load();
  assert.strictEqual(sheet.signature(VECTOR.secret, VECTOR.name, VECTOR.client, VECTOR.ts, VECTOR.nonce), VECTOR.sig);
  const url = sheet.feedUrl('https://office.example.org', VECTOR.secret, 'members', 1700000000999, VECTOR.nonce);
  assert.strictEqual(url, `https://office.example.org/feeds/members.json?client=sheet&ts=1700000000&nonce=${VECTOR.nonce}&sig=${VECTOR.sig}`);
});

test('a feed becomes a protected tab with a bold header', () => {
  const written = {};
  const protection = { editors: [{ getEmail: () => 'someone@example.org' }], removed: [], desc: '', domain: true,
    setDescription(d) { this.desc = d; }, addEditor() {}, getEditors() { return this.editors; }, removeEditors(list) { this.removed = list; },
    canDomainEdit() { return this.domain; }, setDomainEdit(v) { this.domain = v; } };
  const tab = {
    clearContents() { written.cleared = true; },
    getRange(row, col, rows, cols) {
      return { setValues: (v) => { written.values = v; }, setFontWeight: (w) => { written.bold = [row, cols, w]; }, setValue: (v) => { written.note = [row, v]; } };
    },
    setFrozenRows(n) { written.frozen = n; },
    getProtections: () => [],
    protect: () => protection
  };
  const book = { getSheetByName: () => null, insertSheet: (title) => { written.title = title; return tab; } };
  const sheet = load({ SpreadsheetApp: { ProtectionType: { SHEET: 'SHEET' } }, Session: { getEffectiveUser: () => ({ getEmail: () => 'owner@example.org' }) } });
  sheet.writeTab(book, 'Members', { columns: ['Name', 'Money'], rows: [['Ann Sailor', 'Paid up']], generated_at: '2026-10-25T12:00:00+00:00' });
  assert.strictEqual(JSON.stringify(written.values), JSON.stringify([['Name', 'Money'], ['Ann Sailor', 'Paid up']])); // arrays from the sandbox compare by value
  assert.strictEqual(written.title, 'Members');
  assert.strictEqual(JSON.stringify(written.bold), JSON.stringify([1, 2, 'bold']));
  assert.strictEqual(written.frozen, 1);
  assert.match(written.note[1], /Do not edit here/);
  assert.strictEqual(written.note[0], 4);
  assert.strictEqual(protection.removed.length, 1);
  assert.strictEqual(protection.domain, false);
});

test('every feed the Sheet reads is one the office lets it read', () => {
  const sheet = load();
  const python = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'feeds.py'), 'utf8');
  for (const copy of Object.keys(sheet.COPIES)) {
    for (const [name] of sheet.COPIES[copy]) {
      assert.match(python, new RegExp(`"${name}": \\("[^"]+", \\([^)]*"sheet"`), name);
    }
  }
});

test('money stays out of the committee copy', () => {
  const sheet = load();
  const committee = sheet.feedsFor('committee').map(([name]) => name);
  for (const money of ['chase', 'payments', 'money', 'members']) assert.ok(!committee.includes(money), money);
  assert.strictEqual(sheet.feedsFor(null).length, committee.length); // not set up yet: the safe copy
  assert.ok(sheet.feedsFor('treasurer').some(([name]) => name === 'chase'));
});
