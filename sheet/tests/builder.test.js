/**
 * Opens the "New invoice" sidebar in a real browser (Chromium), uses it like a
 * person would, and checks what it sends back to the spreadsheet. Then feeds
 * that to the real server code. Saves screenshots of the sidebar and of an
 * invoice email.
 *
 *   node tests/builder.test.js [folder for screenshots]
 */
'use strict';

const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');
const { createEnv } = require('./fake-gas');

const outDir = process.argv[2] || path.join(__dirname, 'screenshots');

(async () => {
  const env = createEnv({});
  env.answer({ button: 'OK', text: 'Seatown Sailing Club' });
  env.run('setUpRegister()');
  env.run('addExampleData()');
  env.answer({ button: 'OK', text: 'rk_test_screenshot' });
  env.run('connectStripe()');
  const data = env.run('JSON.stringify(builderData())');
  const html = env.run('builderHtml_()');

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 300, height: 860 }, deviceScaleFactor: 2 });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  // Stand-in for google.script.run (Google provides the real one inside the spreadsheet).
  const stub = '<script>window.__data = ' + data + '; window.__calls = []; window.__fail = null;' +
    'function __runner() { var ok = null, fail = null; var api = {' +
    ' withSuccessHandler: function (f) { ok = f; return api; },' +
    ' withFailureHandler: function (f) { fail = f; return api; },' +
    ' builderData: function () { setTimeout(function () { ok(window.__data); }, 5); },' +
    ' builderCreate: function (p) { window.__calls.push(p); setTimeout(function () { if (window.__fail) fail(new Error(window.__fail));' +
    ' else ok({ invoiceNo: "INV-1042", total: "£45.00", sent: true, message: "Emailed 1 invoice(s)." }); }, 5); } };' +
    ' return api; }' +
    'window.google = { script: {} }; Object.defineProperty(window.google.script, "run", { get: __runner });</script>';
  await page.setContent(html.replace('<head>', '<head>' + stub));
  await page.waitForSelector('#form', { state: 'visible' });

  const results = [];
  const check = async (name, fn) => {
    try { await fn(); results.push('PASS  ' + name); } catch (e) { results.push('FAIL  ' + name + '\n      ' + e.message.split('\n')[0]); }
  };

  await check('Loads members, fees and test-mode notice', async () => {
    assert.strictEqual(await page.locator('#members option').count(), 21);
    assert.match(await page.textContent('#banner'), /Test mode is on: emails go to admin@example\.org/);
    assert.doesNotMatch(await page.textContent('#banner'), /Stripe is not connected/);
    assert.strictEqual(await page.inputValue('#year'), String(JSON.parse(data).year));
  });

  await check('Picking a member shows where the invoice will go', async () => {
    await page.fill('#member', 'Ben Carter (M0002)');
    assert.match(await page.textContent('#memberHint'), /emailed to ben\.carter@example\.com/);
  });

  await check('Choosing a fee fills in the line; adding lines keeps a running total', async () => {
    await page.selectOption('.line >> nth=0 >> select.fee', 'Kayak / SUP rack');
    assert.strictEqual(await page.inputValue('.line >> nth=0 >> .unit'), '30.00');
    assert.match(await page.inputValue('.line >> nth=0 >> .description'), /^Kayak or paddle board rack space \d{4}$/);
    assert.strictEqual(await page.inputValue('#type'), 'Boat park');
    await page.click('#addLine');
    await page.fill('.line >> nth=1 >> .description', 'Gate key deposit (refundable)');
    await page.fill('.line >> nth=1 >> .unit', '7.50');
    await page.fill('.line >> nth=1 >> .qty', '2');
    assert.strictEqual(await page.textContent('#total'), '£45.00');
    assert.strictEqual(await page.textContent('.line >> nth=1 >> .amount'), '£15.00');
  });

  await page.screenshot({ path: path.join(outDir, 'invoice-builder.png'), fullPage: true });

  let payload = null;
  await check('Save and email sends the right details to the spreadsheet', async () => {
    await page.click('#sendButton');
    await page.waitForFunction(() => window.__calls.length === 1);
    payload = await page.evaluate(() => window.__calls[0]);
    assert.strictEqual(payload.memberId, 'M0002');
    assert.strictEqual(payload.type, 'Boat park');
    assert.strictEqual(payload.send, true);
    assert.strictEqual(payload.lines.length, 2);
    assert.deepStrictEqual(payload.lines[1], { code: '', description: 'Gate key deposit (refundable)', qty: '2', unit: '7.50' });
    await page.waitForSelector('#message.ok');
    assert.match(await page.textContent('#message'), /Invoice INV-1042 \(£45\.00\): Emailed 1 invoice/);
    assert.strictEqual(await page.locator('.line').count(), 1, 'form is cleared for the next invoice');
  });

  await check('The spreadsheet accepts exactly what the sidebar sends', async () => {
    env.context.__payload = payload;
    const res = env.run('builderCreate(__payload)');
    assert.strictEqual(res.total, '£45.00');
    assert.strictEqual(res.sent, true, res.message);
    const lines = env.rows('Invoice lines').filter((l) => l['Invoice no.'] === res.invoiceNo);
    assert.deepStrictEqual(lines.map((l) => [l.Qty, l['Unit £']]), [[1, 30], [2, 7.5]]);
    assert.match(env.mail[env.mail.length - 1].htmlBody, /Pay £45\.00 by card/);
  });

  await check('Errors are shown in plain words', async () => {
    await page.fill('#member', 'Nobody in particular');
    await page.click('#draftButton');
    assert.match(await page.textContent('#message'), /Pick a member from the list/);
    await page.check('input[value="other"]');
    assert.ok(await page.isVisible('#billTo'));
    await page.fill('#billTo', 'Visiting Vic');
    await page.fill('.line >> nth=0 >> .description', 'Taster session');
    await page.fill('.line >> nth=0 >> .unit', '25');
    await page.evaluate(() => { window.__fail = 'The total must be more than £0.'; });
    await page.click('#draftButton');
    await page.waitForSelector('#message.err');
    assert.match(await page.textContent('#message'), /The total must be more than £0/);
    assert.strictEqual(await page.isDisabled('#draftButton'), false, 'buttons work again after a problem');
  });

  await check('No JavaScript errors on the page', async () => assert.deepStrictEqual(pageErrors, []));

  // A picture of what a member receives.
  const mail = env.mail[env.mail.length - 1];
  const email = await browser.newPage({ viewport: { width: 700, height: 900 }, deviceScaleFactor: 2 });
  await email.setContent('<div style="padding:24px;background:#fff">' + mail.htmlBody + '</div>');
  await email.screenshot({ path: path.join(outDir, 'invoice-email.png'), fullPage: true });

  await browser.close();
  results.forEach((r) => console.log(r));
  const failed = results.filter((r) => r.startsWith('FAIL')).length;
  console.log('\n' + (results.length - failed) + ' passed, ' + failed + ' failed. Screenshots in ' + outDir);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
