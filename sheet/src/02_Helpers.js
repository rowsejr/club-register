/**
 * CLUB REGISTER: shared helpers
 * ---------------------------------------------------------------------------
 * Reading and writing tabs, settings, money, dates, the log, and messages.
 *
 * Money is handled in pence inside the code (whole numbers, so nothing is lost
 * to rounding) and written to the sheet in pounds.
 */

function ss_() {
  return SpreadsheetApp.getActiveSpreadsheet();
}

function tz_() {
  return ss_().getSpreadsheetTimeZone() || 'Europe/London';
}

// ---------------------------------------------------------------------------
// Tabs as tables
// ---------------------------------------------------------------------------

/**
 * @typedef {{name: string, sheet: GoogleAppsScript.Spreadsheet.Sheet, headers: string[], col: Object<string, number>,
 *            isFormula: boolean[], lastRow: number, rows: Row[]}} Table
 */

/**
 * Reads a tab. Columns are found by their header, so people can move columns
 * around safely. Rows count as used if anything is typed in them (formula
 * columns don't count).
 * @param {string} name
 * @returns {Table}
 */
function table_(name) {
  const sheet = ss_().getSheetByName(name);
  if (!sheet) throw new Error('The "' + name + '" tab is missing. Use Club ▸ Admin ▸ Set up the register.');
  const info = headerInfo_(sheet);
  const headers = info.headers;
  const isFormula = info.isFormula;
  const width = headers.length;
  /** @type {Object<string, number>} */
  const col = {};
  headers.forEach((h, i) => {
    if (h && !(h in col)) col[h] = i;
  });

  const maxRows = sheet.getMaxRows();
  let values = maxRows > 1 ? sheet.getRange(2, 1, maxRows - 1, width).getValues() : [];
  const used = lastUsed_(values, isFormula);
  values = values.slice(0, used);

  const t = { name, sheet, headers, col, isFormula, lastRow: used + 1, rows: /** @type {Row[]} */ ([]) };
  t.rows = values.map((v, i) => makeRow_(t, v, i + 2));
  return t;
}

/** How many rows (from row 2) have something typed in them. Formula columns don't count. */
function lastUsed_(values, isFormula) {
  for (let r = values.length - 1; r >= 0; r--) {
    if (values[r].some((v, i) => !isFormula[i] && v !== '' && v !== null)) return r + 1;
  }
  return 0;
}

/** True if two cell values are the same (allowing for how Sheets stores text, numbers and dates). */
function sameValue_(a, b) {
  const norm = (x) => (x instanceof Date ? 'D' + x.getTime() : String(x === null || x === undefined ? '' : x).replace(/^'/, ''));
  return norm(a) === norm(b);
}

/**
 * The headers of a tab. A formula column's header is read from its formula
 * (={"Owner";…}), so a column whose formula shows an error is still found.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 */
function headerInfo_(sheet) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const head = sheet.getRange(1, 1, 1, width);
  const values = head.getValues()[0];
  const formulas = head.getFormulas()[0];
  const headers = values.map((v, i) => {
    const m = formulas[i] ? formulas[i].match(/^=\{\s*"([^"]+)"/) : null;
    return m ? m[1].trim() : String(v).trim();
  });
  return { headers, isFormula: formulas.map((f) => f !== '') };
}

/** 1 → A, 27 → AA */
function colLetter_(n) {
  let s = '';
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

/** "Jane Smith (M0007)" → "M0007" */
function idFromLabel_(text) {
  const m = String(text || '').match(/\(([A-Z]\d+)\)\s*$/);
  return m ? m[1] : String(text || '').trim();
}

/**
 * @typedef {{n: number, v: any[], get: (h: string) => any, set: (h: string, value: any) => void}} Row
 */

/**
 * One row of a table. get() reads a column; set() writes one cell straight away.
 * @param {Table} t
 * @param {any[]} v
 * @param {number} n
 * @returns {Row}
 */
function makeRow_(t, v, n) {
  return {
    n,
    v,
    get(h) {
      return v[colIndex_(t, h)];
    },
    set(h, value) {
      const i = colIndex_(t, h);
      // Check the row is still the one we read: someone may have sorted the tab or edited it meanwhile.
      const now = t.sheet.getRange(n, 1, 1, t.headers.length).getValues()[0];
      if (t.headers.some((header, j) => header && !t.isFormula[j] && !sameValue_(now[j], v[j]))) {
        throw new Error('Row ' + n + ' of the ' + t.name + ' tab changed while this was running (perhaps the tab was sorted, ' +
          'or someone edited that row). Nothing was written to it. Please run this again.');
      }
      const safe = typeof value === 'string' ? safe_(value) : value;
      t.sheet.getRange(n, i + 1).setValue(safe);
      v[i] = safe;
    },
  };
}

/**
 * @param {Table} t
 * @param {string} h
 */
function colIndex_(t, h) {
  const i = t.col[h];
  if (i === undefined) {
    throw new Error('The ' + t.name + ' tab has no "' + h + '" column. Was a header renamed? ' +
      'Club ▸ Admin ▸ Set up the register puts back missing columns.');
  }
  return i;
}

/**
 * Adds rows to the bottom of a table. Each object maps headers to values.
 * Formula columns are left alone, so their formulas keep working. Text is
 * made safe (see safe_), so nothing typed into a form can become a formula.
 * @param {Table} t
 * @param {Object<string, any>[]} rows
 * @returns {Row[]}
 */
function appendRows_(t, rows) {
  if (!rows.length) return [];
  const objects = rows.map((o) => {
    /** @type {Object<string, any>} */
    const clean = {};
    Object.keys(o).forEach((h) => {
      colIndex_(t, h);
      clean[h] = typeof o[h] === 'string' ? safe_(o[h]) : o[h];
    });
    return clean;
  });
  const width = t.headers.length;
  let start = t.lastRow + 1;
  let end = start + objects.length - 1;
  for (let attempt = 0; attempt < 5; attempt++) {
    end = start + objects.length - 1;
    const maxRows = t.sheet.getMaxRows();
    if (end > maxRows) t.sheet.insertRowsAfter(maxRows, end - maxRows + 100);
    // If someone has typed into these rows since the tab was read, go below what they typed.
    const target = t.sheet.getRange(start, 1, objects.length, width).getValues();
    if (lastUsed_(target, t.isFormula) === 0) break;
    start = lastUsed_(t.sheet.getRange(2, 1, t.sheet.getMaxRows() - 1, width).getValues(), t.isFormula) + 2;
  }

  // Write each run of typed-in columns in one go (fast), skipping formula columns.
  let c = 0;
  while (c < width) {
    if (t.isFormula[c]) {
      c++;
      continue;
    }
    let last = c;
    while (last + 1 < width && !t.isFormula[last + 1]) last++;
    const block = objects.map((o) => {
      const out = [];
      for (let i = c; i <= last; i++) {
        const h = t.headers[i];
        const value = h ? o[h] : undefined;
        out.push(value === undefined || value === null ? '' : value);
      }
      return out;
    });
    t.sheet.getRange(start, c + 1, objects.length, last - c + 1).setValues(block);
    c = last + 1;
  }

  const added = objects.map((o, k) => {
    const v = t.headers.map((h) => (h && o[h] !== undefined && o[h] !== null ? o[h] : ''));
    const row = makeRow_(t, v, start + k);
    t.rows.push(row);
    return row;
  });
  t.lastRow = end;
  return added;
}

/** The rows the person has selected on a tab (row numbers, header excluded). */
function selectedRowNumbers_(tabName) {
  const sheet = ss_().getActiveSheet();
  if (sheet.getName() !== tabName) {
    throw new Error('First go to the ' + tabName + ' tab and click on the row you mean.');
  }
  const ranges = sheet.getActiveRangeList() ? sheet.getActiveRangeList().getRanges() : [sheet.getActiveRange()];
  const rows = {};
  ranges.forEach((r) => {
    if (!r) return;
    for (let i = r.getRow(); i <= r.getLastRow(); i++) if (i > 1) rows[i] = true;
  });
  return Object.keys(rows).map(Number).sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------
// IDs (M0001, B0001, C001 …)
// ---------------------------------------------------------------------------

/**
 * @param {Table} t
 * @param {string} header
 * @param {string} prefix
 * @param {number} digits
 * @param {number} count
 */
function nextIds_(t, header, prefix, digits, count) {
  let max = 0;
  const pattern = new RegExp('^' + prefix + '(\\d+)$');
  t.rows.forEach((r) => {
    const m = String(r.get(header)).trim().match(pattern);
    if (m) max = Math.max(max, Number(m[1]));
  });
  const ids = [];
  for (let i = 1; i <= count; i++) ids.push(prefix + String(max + i).padStart(digits, '0'));
  return ids;
}

/**
 * Gives an ID to any row that has something in it but no ID yet. Returns how many it filled in.
 * @param {Table} t
 * @param {string} header
 * @param {string} prefix
 * @param {number} digits
 * @param {string[]} mustHave
 */
function fillMissingIds_(t, header, prefix, digits, mustHave) {
  const missing = t.rows.filter((r) => String(r.get(header)).trim() === '' &&
    mustHave.some((h) => String(r.get(h)).trim() !== ''));
  if (!missing.length) return 0;
  const ids = nextIds_(t, header, prefix, digits, missing.length);
  missing.forEach((r, i) => r.set(header, ids[i]));
  return missing.length;
}

// ---------------------------------------------------------------------------
// Settings, fees and members
// ---------------------------------------------------------------------------

/** The Settings tab as {name: value}. */
function settings_() {
  const sheet = ss_().getSheetByName(TAB.SETTINGS);
  if (!sheet) throw new Error('The Settings tab is missing. Use Club ▸ Admin ▸ Set up the register.');
  /** @type {Object<string, any>} */
  const s = {};
  sheet.getDataRange().getValues().slice(1).forEach((r) => {
    const name = String(r[0]).trim();
    if (name) s[name] = r[1];
  });
  return s;
}

function setSetting_(name, value) {
  const sheet = ss_().getSheetByName(TAB.SETTINGS);
  if (!sheet) return;
  const names = sheet.getRange(1, 1, Math.max(sheet.getLastRow(), 1), 1).getValues().map((r) => String(r[0]).trim());
  const i = names.indexOf(name);
  if (i >= 0) sheet.getRange(i + 1, 2).setValue(value);
  else sheet.appendRow([name, value, '']);
}

function yes_(value) {
  return String(value).trim().toLowerCase() === 'yes';
}

/** The Fees tab as {code: {category, description, pence}}. */
function feeMap_() {
  /** @type {Object<string, {category: string, description: string, pence: number}>} */
  const fees = {};
  table_(TAB.FEES).rows.forEach((r) => {
    const code = String(r.get('Code')).trim();
    if (!code) return;
    fees[code] = {
      category: String(r.get('Category')).trim(),
      description: String(r.get('Description on invoice')).trim() || code,
      pence: pence_(r.get('Amount £')),
    };
  });
  return fees;
}

/** @param {Row} r */
function fullName_(r) {
  return (String(r.get('First name')).trim() + ' ' + String(r.get('Last name')).trim()).trim();
}

/** The Members tab as {memberId: {...}}. */
function memberMap_() {
  /** @type {Object<string, {id: string, name: string, email: string, status: string, membership: string, row: Row}>} */
  const members = {};
  table_(TAB.MEMBERS).rows.forEach((r) => {
    const id = String(r.get('Member ID')).trim();
    if (!id) return;
    members[id] = {
      id,
      name: fullName_(r),
      email: String(r.get('Email')).trim(),
      status: String(r.get('Status')).trim(),
      membership: String(r.get('Membership')).trim(),
      row: r,
    };
  });
  return members;
}

function memberIdByEmail_(email) {
  const wanted = String(email).trim().toLowerCase();
  if (!wanted) return '';
  const members = memberMap_();
  const ids = Object.keys(members).filter((id) => members[id].email.toLowerCase() === wanted && members[id].status !== 'Left');
  return ids.length === 1 ? ids[0] : '';
}

/** Email addresses of the people holding a role on the Roles tab. */
function roleEmails_(role) {
  const sheet = ss_().getSheetByName(TAB.ROLES);
  if (!sheet) return [];
  return table_(TAB.ROLES).rows
    .filter((r) => String(r.get('Role')).trim() === role)
    .map((r) => String(r.get('Email')).trim())
    .filter(isEmail_);
}

// ---------------------------------------------------------------------------
// Money and dates
// ---------------------------------------------------------------------------

function pence_(pounds) {
  const n = Number(pounds);
  return isFinite(n) ? Math.round(n * 100) : 0;
}

function pounds_(pence) {
  return Math.round(pence) / 100;
}

/** 12345 → "£123.45" */
function money_(pence) {
  const sign = pence < 0 ? '-' : '';
  const fixed = (Math.abs(Math.round(pence)) / 100).toFixed(2);
  const parts = fixed.split('.');
  return sign + '£' + parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + parts[1];
}

/** Midnight (club time) on the same day. */
function dateOnly_(d) {
  const tz = tz_();
  return Utilities.parseDate(Utilities.formatDate(d, tz, 'yyyy-MM-dd'), tz, 'yyyy-MM-dd');
}

function today_() {
  return dateOnly_(new Date());
}

/** Adds days safely across clock changes. */
function addDays_(d, days) {
  const tz = tz_();
  const noon = Utilities.parseDate(Utilities.formatDate(d, tz, 'yyyy-MM-dd') + ' 12:00', tz, 'yyyy-MM-dd HH:mm');
  return dateOnly_(new Date(noon.getTime() + days * 86400000));
}

/** Whole days from a to b (b later = positive). */
function daysBetween_(a, b) {
  return Math.round((dateOnly_(b).getTime() - dateOnly_(a).getTime()) / 86400000);
}

/** "5 January 2027" */
function longDate_(d) {
  return d instanceof Date ? Utilities.formatDate(d, tz_(), 'd MMMM yyyy') : String(d || '');
}

function isEmail_(s) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(s || '').trim());
}

/**
 * Makes typed-in text safe to put in a cell. Text from a form that starts with
 * = + - or @ would otherwise be treated as a formula (a known way to attack
 * spreadsheets), so it is stored as plain text instead.
 */
function safe_(value) {
  const s = String(value === undefined || value === null ? '' : value);
  return /^[=+\-@]/.test(s) ? '\'' + s : s;
}

/** Makes text safe to put inside an email or web page. */
function esc_(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function addNote_(existing, note) {
  const old = String(existing || '').trim();
  return old ? old + ' | ' + note : note;
}

// ---------------------------------------------------------------------------
// Log, people and messages
// ---------------------------------------------------------------------------

/** Who is running this: their email, or "Automatic" for scheduled checks. */
function who_() {
  try {
    return Session.getActiveUser().getEmail() || Session.getEffectiveUser().getEmail() || 'Automatic';
  } catch (e) {
    return 'Automatic';
  }
}

/** Adds a line to the Log tab. Never stops the work if the log can't be written. */
function log_(what, details) {
  try {
    const sheet = ss_().getSheetByName(TAB.LOG);
    if (sheet) sheet.appendRow([new Date(), who_(), what, String(details === undefined ? '' : details).slice(0, 40000)]);
  } catch (e) {
    console.error('Could not write to the log: ' + e);
  }
}

function ui_() {
  return SpreadsheetApp.getUi();
}

function alert_(title, message) {
  ui_().alert(title, message, ui_().ButtonSet.OK);
}

function confirm_(title, message) {
  return ui_().alert(title, message, ui_().ButtonSet.YES_NO) === ui_().Button.YES;
}

/**
 * Runs a menu action. If anything goes wrong, the person gets a plain message
 * and the details go in the Log tab.
 */
function withUi_(title, action) {
  try {
    return action();
  } catch (e) {
    const err = /** @type {Error} */ (e);
    log_('Problem: ' + title, err && err.stack ? err.stack : String(e));
    alert_(title, 'That did not work:\n\n' + (err && err.message ? err.message : String(e)) +
      '\n\nAnything finished before the problem has been kept. The Log tab has the details.');
    return undefined;
  }
}

/** A short list for a message box: the first few items, then "and 7 more". */
function listText_(items, max) {
  const limit = max || 8;
  if (!items.length) return '';
  const shown = items.slice(0, limit).map((s) => '• ' + s).join('\n');
  return items.length > limit ? shown + '\n• and ' + (items.length - limit) + ' more (see the Log tab)' : shown;
}
