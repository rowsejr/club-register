/**
 * CLUB REGISTER: setting up the spreadsheet
 * ---------------------------------------------------------------------------
 * "Club ▸ Admin ▸ Set up the register" builds every tab from the description
 * in 00_Config.js. It is safe to run again at any time: it only adds what is
 * missing and refreshes formulas, dropdowns and formatting. It never deletes
 * or changes data.
 */

function setUpRegister() {
  withUi_('Set up the register', () => {
    const ss = ss_();
    const firstTime = !ss.getSheetByName(TAB.SETTINGS);
    let clubName = '';
    if (firstTime) {
      const answer = ui_().prompt('Set up the register', 'What is the club called?', ui_().ButtonSet.OK_CANCEL);
      if (answer.getSelectedButton() !== ui_().Button.OK) return;
      clubName = answer.getResponseText().trim() || 'Our Sailing Club';
    }
    buildRegister_(clubName);
    const protectionProblems = protectHeaders_();
    log_('Register set up', firstTime ? 'First set-up for ' + clubName : 'Checked tabs, columns, formulas and dropdowns');
    ss.getSheetByName(TAB.START).activate();
    alert_('Set up the register', (firstTime
      ? 'Done. Have a look at the Start here tab.\n\nTo try things out with made-up members, use Club ▸ Admin ▸ Add example data. ' +
        'Test mode is on, so emails only go to you.'
      : 'Done. Any missing tabs, columns, dropdowns or formulas have been put back. No data was changed.') +
      (protectionProblems.length ? '\n\nNote:\n' + listText_(protectionProblems) : ''));
  });
}

function buildRegister_(clubName) {
  const ss = ss_();
  ss.setSpreadsheetLocale('en_GB');
  ss.setSpreadsheetTimeZone('Europe/London');

  // 1. Every tab, with every header (plain text for now).
  TAB_ORDER.forEach((name, i) => {
    let sheet = ss.getSheetByName(name);
    if (!sheet) sheet = ss.insertSheet(name, Math.min(i, ss.getSheets().length));
    if (SCHEMA[name]) ensureHeaders_(sheet, SCHEMA[name].cols.map((c) => c.h));
  });
  SpreadsheetApp.flush();

  // 2. Formulas, dropdowns, formats and help notes.
  TAB_ORDER.forEach((name) => {
    if (SCHEMA[name]) styleTab_(/** @type {GoogleAppsScript.Spreadsheet.Sheet} */ (ss.getSheetByName(name)), name, SCHEMA[name]);
  });

  // 3. Settings, roles and the Start here page.
  fillSettings_(clubName);
  fillRoles_();
  writeStartHere_();
  removeEmptyDefaultSheet_();
  SpreadsheetApp.flush();
}

/**
 * Adds any missing headers at the right-hand end of row 1.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {string[]} wanted
 */
function ensureHeaders_(sheet, wanted) {
  const existing = headerInfo_(sheet).headers;
  const missing = wanted.filter((h) => existing.indexOf(h) < 0);
  if (!missing.length) return;
  const first = existing.every((h) => h === '') ? 1 : sheet.getLastColumn() + 1;
  const needed = first + missing.length - 1;
  if (needed > sheet.getMaxColumns()) sheet.insertColumnsAfter(sheet.getMaxColumns(), needed - sheet.getMaxColumns());
  sheet.getRange(1, first, 1, missing.length).setValues([missing]);
}

/** Lets column formulas refer to columns by header: c.L('Qty') → 'Invoice lines'!D2:D */
function formulaContext_(tabName) {
  /** @type {Object<string, string[]>} */
  const cache = {};
  const range = (tab, h) => {
    if (!cache[tab]) cache[tab] = headerInfo_(/** @type {GoogleAppsScript.Spreadsheet.Sheet} */ (ss_().getSheetByName(tab))).headers;
    const i = cache[tab].indexOf(h);
    if (i < 0) throw new Error('Cannot find the "' + h + '" column on the ' + tab + ' tab.');
    const L = colLetter_(i + 1);
    return '\'' + tab + '\'!' + L + '2:' + L;
  };
  return { L: (h) => range(tabName, h), R: (tab, h) => range(tab, h) };
}

/**
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {string} name
 * @param {{owner: string, freezeColumns?: number, cols: {h: string, w?: number, note?: string, type?: string, list?: string, f?: Function}[]}} spec
 */
function styleTab_(sheet, name, spec) {
  const headers = headerInfo_(sheet).headers;
  /** @type {Object<string, number>} */
  const pos = {};
  headers.forEach((h, i) => {
    if (h && !(h in pos)) pos[h] = i + 1;
  });
  const maxRows = sheet.getMaxRows();
  sheet.getRange(1, 1, 1, headers.length)
    .setFontWeight('bold').setBackground('#e8eaed').setVerticalAlignment('middle').setWrap(true);
  sheet.setFrozenRows(1);
  if (spec.freezeColumns) sheet.setFrozenColumns(spec.freezeColumns);
  sheet.setTabColor(ROLE_COLOUR[spec.owner] || '#999999');

  const ctx = formulaContext_(name);
  spec.cols.forEach((col) => {
    const c = pos[col.h];
    if (!c) return;
    const head = sheet.getRange(1, c);
    const body = maxRows > 1 ? sheet.getRange(2, c, maxRows - 1, 1) : null;
    if (col.w) sheet.setColumnWidth(c, col.w);
    if (col.f) {
      head.setFormula('={"' + col.h + '";' + col.f(ctx) + '}').setBackground('#d9ead3');
      if (body) body.setBackground('#f3f3f3');
    }
    head.setNote((col.f ? 'Worked out automatically. Please don\'t type in this column.\n\n' : '') + (col.note || ''));
    if (!body) return;
    if (col.type === 'money') body.setNumberFormat('£#,##0.00');
    if (col.type === 'date') body.setNumberFormat('dd/mm/yyyy');
    if (col.type === 'time') body.setNumberFormat('hh:mm');
    if (col.type === 'whole') body.setNumberFormat('0');
    if (col.type === 'text') body.setNumberFormat('@');
    const rule = col.f ? null : validationFor_(col);
    if (rule) body.setDataValidation(rule);
    else body.clearDataValidations();
  });
  applyConditionalFormats_(sheet, name, pos, maxRows);
}

/** The dropdown or check for a column, if it has one. */
function validationFor_(col) {
  const b = SpreadsheetApp.newDataValidation();
  if (col.list && LISTS[col.list]) {
    return b.requireValueInList(LISTS[col.list], true).setAllowInvalid(false).build();
  }
  if (col.list && LIST_RANGES[col.list]) {
    const tab = LIST_RANGES[col.list][0];
    const header = LIST_RANGES[col.list][1];
    const source = /** @type {GoogleAppsScript.Spreadsheet.Sheet} */ (ss_().getSheetByName(tab));
    const i = headerInfo_(source).headers.indexOf(header);
    if (i < 0) return null;
    const L = colLetter_(i + 1);
    return b.requireValueInRange(source.getRange(L + '2:' + L), true).setAllowInvalid(false).build();
  }
  if (col.type === 'date') return b.requireDate().setAllowInvalid(true).setHelpText('A date, like 05/01/2027.').build();
  if (col.type === 'email') return b.requireTextIsEmail().setAllowInvalid(true).setHelpText('An email address.').build();
  if (col.type === 'money') {
    return b.requireNumberBetween(-100000, 100000).setAllowInvalid(false).setHelpText('An amount in pounds, like 95 or 12.50.').build();
  }
  if (col.type === 'whole') return b.requireNumberGreaterThanOrEqualTo(0).setAllowInvalid(false).setHelpText('A whole number.').build();
  return null;
}

/**
 * Colours that make the state of things obvious: overdue in red, paid in green, and so on.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {string} name
 * @param {Object<string, number>} pos
 * @param {number} maxRows
 */
function applyConditionalFormats_(sheet, name, pos, maxRows) {
  if (maxRows < 2) return;
  /** @type {GoogleAppsScript.Spreadsheet.ConditionalFormatRule[]} */
  const rules = [];
  const column = (h) => sheet.getRange(2, pos[h], maxRows - 1, 1);
  const allColumns = sheet.getRange(2, 1, maxRows - 1, Math.max(sheet.getLastColumn(), 1));
  const letter = (h) => colLetter_(pos[h]);
  const whenText = (h, value, background, font, strike) => {
    if (!pos[h]) return;
    let rule = SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo(value).setRanges([column(h)]);
    if (background) rule = rule.setBackground(background);
    if (font) rule = rule.setFontColor(font);
    if (strike) rule = rule.setStrikethrough(true);
    rules.push(rule.build());
  };
  const greyRowWhen = (h, value) => {
    if (!pos[h]) return;
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=$' + letter(h) + '2="' + value + '"')
      .setFontColor('#999999').setRanges([allColumns]).build());
  };

  if (name === TAB.INVOICES || name === TAB.BOOKINGS) {
    const h = name === TAB.INVOICES ? 'Status' : 'Payment';
    whenText(h, 'Paid', '#d9ead3', '#274e13');
    whenText(h, 'Overpaid', '#d9ead3', '#274e13');
    whenText(h, 'Part paid', '#fff2cc');
    whenText(h, 'Unpaid', '#fce5cd');
    whenText(h, 'Overdue', '#f4cccc', '#990000');
    whenText(h, 'Draft', '#eeeeee', '#666666');
    whenText(h, 'Cancelled', '#eeeeee', '#999999', true);
  }
  if (name === TAB.MEMBERS) greyRowWhen('Status', 'Left');
  if (name === TAB.BOATS) {
    greyRowWhen('Status', 'Left');
    if (pos['Insurance expiry']) {
      const L = letter('Insurance expiry');
      rules.push(SpreadsheetApp.newConditionalFormatRule()
        .whenFormulaSatisfied('=AND(' + L + '2<>"",' + L + '2<TODAY())')
        .setBackground('#f4cccc').setRanges([column('Insurance expiry')]).build());
    }
  }
  if (name === TAB.COURSES && pos['Places left'] && pos['Course ID']) {
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND(' + letter('Course ID') + '2<>"",' + letter('Places left') + '2<=0)')
      .setBackground('#f4cccc').setRanges([column('Places left')]).build());
  }
  if (name === TAB.APPLICATIONS) whenText('Decision', 'New', '#fff2cc');
  if (name === TAB.CALENDAR) greyRowWhen('Status', 'Cancelled');
  sheet.setConditionalFormatRules(rules);
}

/** Adds any missing settings, with their defaults. Existing values are never changed. */
function fillSettings_(clubName) {
  const sheet = /** @type {GoogleAppsScript.Spreadsheet.Sheet} */ (ss_().getSheetByName(TAB.SETTINGS));
  const names = sheet.getRange(1, 1, Math.max(sheet.getLastRow(), 1), 1).getValues().map((r) => String(r[0]).trim());
  const now = new Date();
  /** @type {Object<string, any>} */
  const defaults = {
    'Club name': clubName || 'Our Sailing Club',
    'Membership year': now.getMonth() >= 9 ? now.getFullYear() + 1 : now.getFullYear(),
    'Test email address': Session.getEffectiveUser().getEmail(),
  };
  const missing = SETTINGS_LIST.filter((s) => names.indexOf(String(s[0])) < 0)
    .map((s) => [s[0], s[1] === null ? defaults[String(s[0])] : s[1], s[2]]);
  if (missing.length) sheet.getRange(sheet.getLastRow() + 1, 1, missing.length, 3).setValues(missing);

  const all = sheet.getRange(1, 1, sheet.getLastRow(), 1).getValues().map((r) => String(r[0]).trim());
  const yesNo = SpreadsheetApp.newDataValidation().requireValueInList(LISTS.yesNo, true).setAllowInvalid(false).build();
  all.forEach((name, i) => {
    if (i === 0) return;
    const row = i + 1;
    if (['Test mode', 'Show bank details on invoices', 'Attach PDF invoice', 'Invoice course bookings automatically'].indexOf(name) >= 0) {
      sheet.getRange(row, 2).setDataValidation(yesNo);
    }
    if (name === 'Sort code' || name === 'Account number') sheet.getRange(row, 2).setNumberFormat('@');
  });
  sheet.getRange(2, 1, all.length - 1, 1).setFontWeight('bold');
  sheet.getRange(2, 2, all.length - 1, 2).setWrap(true).setVerticalAlignment('top');
  sheet.getRange(2, 3, all.length - 1, 1).setFontColor('#666666');
}

function fillRoles_() {
  const t = table_(TAB.ROLES);
  if (t.rows.length) return;
  const me = Session.getEffectiveUser().getEmail();
  appendRows_(t, ROLES_LIST.map((r, i) => ({ 'Role': r[0], 'Email': i === 0 ? me : '', 'Looks after': r[1] })));
}

function removeEmptyDefaultSheet_() {
  const ss = ss_();
  ss.getSheets().forEach((sheet) => {
    if (TAB_ORDER.indexOf(sheet.getName()) >= 0 || ss.getSheets().length <= 1) return;
    if (/^(Sheet|Blad|Feuille|Hoja|Tabelle)\s*1$/i.test(sheet.getName()) && sheet.getLastRow() === 0 && sheet.getLastColumn() === 0) {
      ss.deleteSheet(sheet);
    }
  });
}

/**
 * Header rows can only be changed by Admins (a renamed header or an
 * overwritten formula would break things). Everything below the header stays
 * editable, and Settings, Roles and the Log ask "are you sure?" before edits.
 * Returns a list of problems (e.g. an Admin who has not been given access).
 */
function protectHeaders_() {
  const ss = ss_();
  const me = Session.getEffectiveUser().getEmail();
  const admins = roleEmails_(ROLE.ADMIN).filter((e) => e.toLowerCase() !== me.toLowerCase());
  const problems = [];
  TAB_ORDER.forEach((name) => {
    const sheet = ss.getSheetByName(name);
    if (!sheet) return;
    sheet.getProtections(SpreadsheetApp.ProtectionType.RANGE).concat(sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET))
      .forEach((p) => {
        if (String(p.getDescription()).indexOf('Club:') === 0) p.remove();
      });

    if (name === TAB.SETTINGS || name === TAB.ROLES || name === TAB.LOG || name === TAB.START) {
      sheet.protect().setDescription('Club: ' + name + ' (please check before changing)').setWarningOnly(true);
    }
    if (!SCHEMA[name]) return;
    const p = sheet.getRange(1, 1, 1, sheet.getMaxColumns()).protect().setDescription('Club: headers of ' + name);
    p.addEditor(me);
    p.removeEditors(p.getEditors().map((u) => u.getEmail()).filter((e) => e && e.toLowerCase() !== me.toLowerCase()));
    if (p.canDomainEdit()) p.setDomainEdit(false);
    admins.forEach((email) => {
      try {
        p.addEditor(email);
      } catch (e) {
        problems.push(email + ' is listed as an Admin but cannot edit this spreadsheet yet (share it with them first).');
      }
    });
  });
  return problems.filter((v, i, a) => a.indexOf(v) === i);
}

/** Menu item: run the protection again, e.g. after the Admins on the Roles tab change. */
function lockTabs() {
  withUi_('Protect headers and settings', () => {
    const problems = protectHeaders_();
    log_('Headers protected', problems.join(' | '));
    alert_('Protect headers and settings', 'Done. Header rows can now only be changed by the Admins on the Roles tab. ' +
      'Settings, Roles and the Log ask "are you sure?" before anyone edits them.' +
      (problems.length ? '\n\nNote:\n' + listText_(problems) : ''));
  });
}

// ---------------------------------------------------------------------------
// The Start here tab
// ---------------------------------------------------------------------------

const GOLDEN_RULES = [
  ['Never delete a row.', 'Mark members and boats as Left. Cancel invoices with Club ▸ Invoices ▸ Cancel selected invoice. The history stays and nothing gets out of step.'],
  ['Don\'t type in grey columns.', 'They are worked out from the other tabs. Hover over a header to see how.'],
  ['Use the Club menu for invoices.', 'It numbers them, emails them, adds the card payment link and keeps the books straight.'],
  ['Check test mode.', 'While Settings says Test mode: Yes, emails only go to the test address. Set it to No when you go live.'],
  ['Mistakes can be undone.', 'File ▸ Version history shows every earlier version of this spreadsheet and can put it back.'],
];

const HOW_TO = [
  [ROLE.TREASURER, [
    ['January: membership invoices', '1. Check the prices on the Fees tab. 2. Club ▸ Membership ▸ Prepare membership invoices. ' +
      '3. Check the Draft rows on the Invoices tab. 4. Club ▸ Invoices ▸ Send draft invoices. Gmail allows about 100 emails a day, ' +
      'so if there are more, send again the next day.'],
    ['Then: boat park invoices', 'Club ▸ Boat park ▸ Prepare boat park invoices, check the drafts, then Send draft invoices. ' +
      'Running it again later in the year only charges boats that have not been charged yet.'],
    ['A one-off invoice', 'Club ▸ Invoices ▸ New invoice. Pick the person, add lines, then save it as a draft or email it straight away.'],
    ['Card payments', 'Nothing to do. They appear on the Payments tab by themselves within 15 minutes (or use Club ▸ Payments ▸ Update payments now).'],
    ['A bank payment', 'Add a row on the Payments tab: date, invoice number, amount, method Bank transfer, and the bank reference.'],
    ['A refund', 'Refund it in Stripe, then add a row on the Payments tab with a minus amount and method Refund.'],
    ['Chasing unpaid invoices', 'Club ▸ Invoices ▸ Send reminders. It only reminds people whose invoice is overdue and who have not been reminded recently.'],
    ['An invoice is wrong', 'Still a draft: change its lines on the Invoice lines tab. Already sent: Club ▸ Invoices ▸ Cancel selected invoice, then make a new one.'],
  ]],
  [ROLE.MEMBERSHIP, [
    ['Someone applies', 'Their form arrives on the Applications tab. To accept them, click their row and use Club ▸ Membership ▸ Accept selected application. ' +
      'They get a Member ID and an invoice by email, and become Active when they pay.'],
    ['Someone joins without the form', 'Add them on the Members tab with Status Applicant (the Member ID fills itself in), ' +
      'then Club ▸ Invoices ▸ New invoice with type Membership.'],
    ['Someone leaves or does not renew', 'Set their Status to Left or Lapsed. Never delete the row.'],
  ]],
  [ROLE.BOATPARK, [
    ['A new boat', 'Add a row on the Boats tab (or it arrives from the boat form): owner, space, boat park fee and Status In park.'],
    ['A boat leaves', 'Set its Status to Left. Keep the row.'],
  ]],
  [ROLE.TRAINING, [
    ['A new course', 'Add a row on the Courses tab with Status Open, then Club ▸ Training ▸ Update the booking form.'],
    ['Bookings', 'They arrive on the Bookings tab. If there is a place, the person is emailed an invoice straight away. ' +
      'When a course is full, new bookings go on the waiting list.'],
    ['Offering a waiting-list place', 'Click the booking and use Club ▸ Training ▸ Invoice selected booking.'],
  ]],
  [ROLE.EVENTS, [
    ['Changing the calendar', 'Add or change rows on the Calendar tab, then Club ▸ Calendar ▸ Publish the calendar. ' +
      'To remove an event, set its Status to Cancelled and publish.'],
  ]],
  [ROLE.ADMIN, [
    ['Someone takes on a role', 'Share this spreadsheet with them (Share button, as Editor) and put their name and email on the Roles tab. ' +
      'For a new Admin, then use Club ▸ Admin ▸ Protect headers and settings.'],
    ['Going live', 'Settings: set Test mode to No. Club ▸ Admin ▸ Connect Stripe with the live key. Check the prices on the Fees tab.'],
  ]],
];

function writeStartHere_() {
  const sheet = /** @type {GoogleAppsScript.Spreadsheet.Sheet} */ (ss_().getSheetByName(TAB.START));
  const s = settings_();
  /** @type {any[][]} */
  const rows = [];
  /** @type {string[]} */
  const styles = [];
  const add = (a, b, c, style) => {
    rows.push([a || '', b || '', c || '']);
    styles.push(style || '');
  };

  add(String(s['Club name'] || 'Club') + ' register', '', '', 'title');
  add('Members, boats, invoices, payments, training and the calendar, all in one spreadsheet. Each tab is looked after by one role ' +
    '(see the Roles tab). Everything automatic is on the Club menu at the top of the screen.', '', '', 'para');
  add('', '', '', '');
  add('The tabs', '', '', 'heading');
  add('Tab', 'Looked after by', 'What is in it', 'subhead');
  TAB_ORDER.filter((t) => SCHEMA[t]).forEach((t) => add(t, SCHEMA[t].owner, SCHEMA[t].about, ''));
  add('', '', '', '');
  add('Golden rules', '', '', 'heading');
  GOLDEN_RULES.forEach((r, i) => add((i + 1) + '. ' + r[0], '', r[1], 'rule'));
  add('', '', '', '');
  add('How to …', '', '', 'heading');
  HOW_TO.forEach((group) => {
    add(String(group[0]), '', '', 'subhead');
    /** @type {string[][]} */ (group[1]).forEach((item) => add(item[0], '', item[1], ''));
  });

  sheet.getRange(1, 1, Math.max(sheet.getMaxRows(), 1), 3).breakApart();
  sheet.clear();
  sheet.getRange(1, 1, rows.length, 3).setValues(rows).setWrap(true).setVerticalAlignment('top').setFontSize(11);
  sheet.setColumnWidth(1, 290);
  sheet.setColumnWidth(2, 170);
  sheet.setColumnWidth(3, 640);
  styles.forEach((style, i) => {
    const r = sheet.getRange(i + 1, 1, 1, 3);
    if (style === 'title') r.setFontSize(20).setFontWeight('bold');
    if (style === 'para') sheet.getRange(i + 1, 1, 1, 3).merge();
    if (style === 'heading') r.setFontSize(14).setFontWeight('bold').setFontColor('#1a5fb4');
    if (style === 'subhead') r.setFontWeight('bold').setBackground('#e8eaed');
    if (style === 'rule') sheet.getRange(i + 1, 1).setFontWeight('bold');
  });
  sheet.setHiddenGridlines(true);
  sheet.setTabColor('#e06666');
}
