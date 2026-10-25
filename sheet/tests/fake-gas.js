/**
 * A small stand-in for Google Apps Script (Sheets, Gmail, Forms, Calendar,
 * triggers, properties) and for Stripe, so the register's code can be run and
 * checked on an ordinary computer with Node.
 *
 * It does not calculate spreadsheet formulas (tests/formulas.test.js does
 * that separately). Every Stripe request is checked against Stripe's own API
 * description, if spec3.json is available (see tests/README.md).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ---------------------------------------------------------------------------
// Dates in a time zone (Utilities.formatDate / parseDate)
// ---------------------------------------------------------------------------

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const pad = (n) => String(n).padStart(2, '0');

function tzParts(date, tz) {
  const fmt = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
  const p = {};
  fmt.formatToParts(date).forEach((x) => { p[x.type] = x.value; });
  return { y: +p.year, M: +p.month, d: +p.day, H: +p.hour % 24, m: +p.minute, s: +p.second };
}

function formatDate(date, tz, pattern) {
  if (!(date instanceof Date) || isNaN(date.getTime())) throw new Error('formatDate: not a date: ' + date);
  const p = tzParts(date, tz);
  return pattern.replace(/yyyy|MMMM|MM|dd|d|HH|mm|ss/g, (t) => ({
    yyyy: String(p.y), MMMM: MONTHS[p.M - 1], MM: pad(p.M), dd: pad(p.d), d: String(p.d), HH: pad(p.H), mm: pad(p.m), ss: pad(p.s),
  })[t]);
}

function zoned(y, M, d, H, mi, tz) {
  const wanted = Date.UTC(y, M - 1, d, H, mi);
  let guess = wanted;
  for (let i = 0; i < 4; i++) {
    const p = tzParts(new Date(guess), tz);
    const diff = Date.UTC(p.y, p.M - 1, p.d, p.H, p.m) - wanted;
    if (diff === 0) break;
    guess -= diff;
  }
  return new Date(guess);
}

function parseDate(str, tz, pattern) {
  let m;
  if (pattern === 'yyyy-MM-dd' && (m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})$/))) return zoned(+m[1], +m[2], +m[3], 0, 0, tz);
  if (pattern === 'yyyy-MM-dd HH:mm' && (m = String(str).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/))) {
    return zoned(+m[1], +m[2], +m[3], +m[4], +m[5], tz);
  }
  throw new Error('Utilities.parseDate (fake) could not parse "' + str + '" with "' + pattern + '"');
}

// ---------------------------------------------------------------------------
// Spreadsheet
// ---------------------------------------------------------------------------

function a1ToRC(a1, sheet) {
  const m = String(a1).match(/^([A-Z]+)(\d+)?(?::([A-Z]+)(\d+)?)?$/);
  if (!m) throw new Error('fake getRange: unsupported A1 "' + a1 + '"');
  const col = (s) => s.split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
  const c1 = col(m[1]);
  const r1 = m[2] ? +m[2] : 1;
  const c2 = m[3] ? col(m[3]) : c1;
  const r2 = m[4] ? +m[4] : (m[3] ? sheet.maxRows : r1);
  return [r1, c1, r2 - r1 + 1, c2 - c1 + 1];
}

function headerFromFormula(f) {
  const m = String(f).match(/^=\{\s*"([^"]+)"/);
  return m ? m[1] : '';
}

class FakeProtection {
  constructor(sheet, range, type) {
    this.sheet = sheet; this.range = range; this.type = type; this.description = ''; this.warningOnly = false;
    this.editors = new Set(['admin@example.org']); this.domainEdit = true;
  }
  setDescription(d) { this.description = d; return this; }
  getDescription() { return this.description; }
  setWarningOnly(b) { this.warningOnly = !!b; return this; }
  isWarningOnly() { return this.warningOnly; }
  addEditor(e) {
    const email = typeof e === 'string' ? e : e.getEmail();
    if (!this.sheet.ss.env.editors.has(email)) throw new Error('fake: ' + email + ' is not an editor of the spreadsheet');
    this.editors.add(email); return this;
  }
  addEditors(list) { list.forEach((e) => this.addEditor(e)); return this; }
  removeEditors(list) { list.forEach((e) => this.editors.delete(typeof e === 'string' ? e : e.getEmail())); return this; }
  getEditors() { return [...this.editors].map((e) => ({ getEmail: () => e })); }
  canDomainEdit() { return this.domainEdit; }
  setDomainEdit(b) { this.domainEdit = b; return this; }
  remove() { this.sheet.protections = this.sheet.protections.filter((p) => p !== this); }
  getProtectionType() { return this.type; }
}

class FakeRange {
  constructor(sheet, row, col, numRows, numCols) {
    if (row < 1 || col < 1 || numRows < 1 || numCols < 1) throw new Error('fake getRange: bad coordinates ' + [row, col, numRows, numCols]);
    if (row + numRows - 1 > sheet.maxRows || col + numCols - 1 > sheet.maxCols) {
      throw new Error('fake getRange: outside the sheet "' + sheet.name + '" (' + [row, col, numRows, numCols] + ' vs ' + sheet.maxRows + 'x' + sheet.maxCols + ')');
    }
    Object.assign(this, { sheet, row, col, numRows, numCols });
  }
  forEach(fn) { for (let r = 0; r < this.numRows; r++) for (let c = 0; c < this.numCols; c++) fn(this.row + r, this.col + c, r, c); }
  getValues() {
    const out = [];
    for (let r = 0; r < this.numRows; r++) {
      const row = [];
      for (let c = 0; c < this.numCols; c++) {
        const cell = this.sheet.cell(this.row + r, this.col + c, false);
        if (!cell) row.push('');
        else if (cell.f) row.push(this.row + r === 1 ? headerFromFormula(cell.f) : '');
        else row.push(cell.v === undefined || cell.v === null ? '' : cell.v);
      }
      out.push(row);
    }
    return out;
  }
  getDisplayValues() {
    const tz = this.sheet.ss.tz;
    return this.getValues().map((row) => row.map((v) => (v instanceof Date ? formatDate(v, tz, 'dd/MM/yyyy') : String(v))));
  }
  getValue() { return this.getValues()[0][0]; }
  getFormulas() {
    const out = [];
    for (let r = 0; r < this.numRows; r++) {
      const row = [];
      for (let c = 0; c < this.numCols; c++) {
        const cell = this.sheet.cell(this.row + r, this.col + c, false);
        row.push(cell && cell.f ? cell.f : '');
      }
      out.push(row);
    }
    return out;
  }
  setValues(values) {
    if (!Array.isArray(values) || values.length !== this.numRows) throw new Error('fake setValues: wrong number of rows (' + (values && values.length) + ' vs ' + this.numRows + ')');
    values.forEach((row) => {
      if (row.length !== this.numCols) throw new Error('fake setValues: wrong number of columns (' + row.length + ' vs ' + this.numCols + ')');
    });
    this.forEach((r, c, i, j) => this.sheet.write(r, c, values[i][j]));
    return this;
  }
  setValue(v) { this.forEach((r, c) => this.sheet.write(r, c, v)); return this; }
  setFormula(f) {
    const cell = this.sheet.cell(this.row, this.col, true);
    cell.f = f; cell.v = '';
    this.sheet.checkArrayCollision(this.row, this.col);
    return this;
  }
  setNote(n) { this.forEach((r, c) => { this.sheet.cell(r, c, true).note = n; }); return this; }
  setDataValidation(rule) { this.forEach((r, c) => { this.sheet.cell(r, c, true).dv = rule; }); return this; }
  clearDataValidations() { this.forEach((r, c) => { const cell = this.sheet.cell(r, c, false); if (cell) delete cell.dv; }); return this; }
  setNumberFormat(fmt) { this.forEach((r, c) => { this.sheet.cell(r, c, true).fmt = fmt; }); return this; }
  setFontWeight() { return this; }
  setBackground() { return this; }
  setFontColor() { return this; }
  setFontSize() { return this; }
  setVerticalAlignment() { return this; }
  setWrap() { return this; }
  merge() { this.sheet.merges.push([this.row, this.col, this.numRows, this.numCols]); return this; }
  breakApart() { this.sheet.merges = []; return this; }
  protect() {
    const p = new FakeProtection(this.sheet, this, 'RANGE');
    this.sheet.protections.push(p);
    return p;
  }
  getRow() { return this.row; }
  getLastRow() { return this.row + this.numRows - 1; }
  getColumn() { return this.col; }
  getNumRows() { return this.numRows; }
  getNumColumns() { return this.numCols; }
  getSheet() { return this.sheet; }
}

class FakeSheet {
  constructor(ss, name, id) {
    Object.assign(this, {
      ss, name, id, cells: new Map(), maxRows: 1000, maxCols: 26, frozenRows: 0, frozenCols: 0, tabColor: null,
      protections: [], conditionalRules: [], merges: [], widths: {}, formUrl: null, hiddenGridlines: false,
    });
  }
  key(r, c) { return r + ',' + c; }
  cell(r, c, create) {
    const k = this.key(r, c);
    let cell = this.cells.get(k);
    if (!cell && create) { cell = {}; this.cells.set(k, cell); }
    return cell;
  }
  write(r, c, v) {
    const cell = this.cell(r, c, true);
    delete cell.f;
    cell.v = v === null || v === undefined ? '' : v;
    if (cell.v !== '') this.checkArrayCollision(r, c);
  }
  /** Real Sheets breaks an array formula if anything is typed below its header. Treat that as a test failure. */
  checkArrayCollision(r, c) {
    if (r === 1) return;
    const head = this.cell(1, c, false);
    if (head && head.f && /ARRAYFORMULA|FILTER/.test(head.f)) {
      const cell = this.cell(r, c, false);
      if (cell && cell.v !== '' && cell.v !== undefined) {
        throw new Error('fake: wrote "' + cell.v + '" into formula column ' + headerFromFormula(head.f) + ' on ' + this.name + ' row ' + r);
      }
    }
  }
  hasContent(cell) { return cell && (cell.f || (cell.v !== '' && cell.v !== undefined && cell.v !== null)); }
  getLastRow() { let m = 0; this.cells.forEach((cell, k) => { if (this.hasContent(cell)) m = Math.max(m, +k.split(',')[0]); }); return m; }
  getLastColumn() { let m = 0; this.cells.forEach((cell, k) => { if (this.hasContent(cell)) m = Math.max(m, +k.split(',')[1]); }); return m; }
  getMaxRows() { return this.maxRows; }
  getMaxColumns() { return this.maxCols; }
  getRange(a, b, c, d) {
    if (typeof a === 'string') { const rc = a1ToRC(a, this); return new FakeRange(this, rc[0], rc[1], rc[2], rc[3]); }
    return new FakeRange(this, a, b, c || 1, d || 1);
  }
  getDataRange() { return new FakeRange(this, 1, 1, Math.max(this.getLastRow(), 1), Math.max(this.getLastColumn(), 1)); }
  insertRowsAfter(after, n) { if (after !== this.maxRows) throw new Error('fake insertRowsAfter: only appending is supported'); this.maxRows += n; return this; }
  insertColumnsAfter(after, n) { if (after !== this.maxCols) throw new Error('fake insertColumnsAfter: only appending is supported'); this.maxCols += n; return this; }
  appendRow(values) {
    const r = this.getLastRow() + 1;
    if (r > this.maxRows) this.maxRows = r;
    if (values.length > this.maxCols) this.maxCols = values.length;
    values.forEach((v, i) => this.write(r, i + 1, v));
    return this;
  }
  getName() { return this.name; }
  setName(n) { if (this.ss.getSheetByName(n)) throw new Error('fake: a sheet called ' + n + ' already exists'); this.name = n; return this; }
  getSheetId() { return this.id; }
  activate() { this.ss.active = this; this.ss.selection = null; return this; }
  setFrozenRows(n) { this.frozenRows = n; return this; }
  setFrozenColumns(n) { this.frozenCols = n; return this; }
  setTabColor(c) { this.tabColor = c; return this; }
  setColumnWidth(c, w) { this.widths[c] = w; return this; }
  setConditionalFormatRules(rules) { this.conditionalRules = rules; return this; }
  getProtections(type) { return this.protections.filter((p) => p.type === type); }
  protect() { const p = new FakeProtection(this, null, 'SHEET'); this.protections.push(p); return p; }
  clear() { this.cells = new Map(); return this; }
  setHiddenGridlines(b) { this.hiddenGridlines = b; return this; }
  getFormUrl() { return this.formUrl; }
  getActiveRange() { const sel = this.ss.selection; return sel && sel.sheet === this ? sel.ranges[0] : this.getRange(1, 1); }
  getActiveRangeList() { const sel = this.ss.selection; return sel && sel.sheet === this ? { getRanges: () => sel.ranges } : null; }
}

class FakeSpreadsheet {
  constructor(env) {
    this.env = env; this.id = 'ss_fake_1'; this.tz = 'America/New_York'; this.locale = 'en_US'; this.nextSheetId = 100;
    this.sheets = [new FakeSheet(this, 'Sheet1', 0)];
    this.active = this.sheets[0];
    this.selection = null;
  }
  getId() { return this.id; }
  getSheetByName(n) { return this.sheets.find((s) => s.name === n) || null; }
  getSheets() { return this.sheets.slice(); }
  insertSheet(name, index) {
    if (this.getSheetByName(name)) throw new Error('fake: sheet exists: ' + name);
    const s = new FakeSheet(this, name, this.nextSheetId++);
    this.sheets.splice(index === undefined ? this.sheets.length : index, 0, s);
    return s;
  }
  deleteSheet(s) { if (this.sheets.length === 1) throw new Error('fake: cannot delete the only sheet'); this.sheets = this.sheets.filter((x) => x !== s); }
  getActiveSheet() { return this.active; }
  setActiveSheet(s) { this.active = s; return s; }
  getNumSheets() { return this.sheets.length; }
  moveActiveSheet(pos) {
    this.sheets = this.sheets.filter((x) => x !== this.active);
    this.sheets.splice(pos - 1, 0, this.active);
  }
  setSpreadsheetLocale(l) { this.locale = l; }
  setSpreadsheetTimeZone(tz) { this.tz = tz; }
  getSpreadsheetTimeZone() { return this.tz; }
}

// ---------------------------------------------------------------------------
// Builders (data validation, conditional formats, menus, forms)
// ---------------------------------------------------------------------------

function chainBuilder(kind, methods) {
  const state = { kind, calls: [] };
  const b = {};
  methods.forEach((m) => { b[m] = (...args) => { state.calls.push([m, args]); return b; }; });
  b.build = () => ({ kind, calls: state.calls.slice() });
  return b;
}

const DV_METHODS = ['requireValueInList', 'requireValueInRange', 'requireDate', 'requireTextIsEmail', 'requireNumberBetween',
  'requireNumberGreaterThanOrEqualTo', 'setAllowInvalid', 'setHelpText'];
const CF_METHODS = ['whenTextEqualTo', 'whenFormulaSatisfied', 'whenNumberLessThanOrEqualTo', 'setBackground', 'setFontColor', 'setStrikethrough', 'setRanges'];

class FakeFormItem {
  constructor(type) { this.type = type; this.title = ''; this.required = false; this.choices = null; this.validation = null; }
  setTitle(t) { this.title = t; return this; }
  getTitle() { return this.title; }
  setRequired(b) { this.required = b; return this; }
  setValidation(v) { this.validation = v; return this; }
  setChoiceValues(list) {
    if (!Array.isArray(list) || !list.length) throw new Error('fake: setChoiceValues needs at least one choice');
    this.choices = list.slice(); return this;
  }
  asListItem() { if (this.type !== 'LIST') throw new Error('fake: not a list item'); return this; }
}

class FakeForm {
  constructor(env, title) {
    this.env = env; this.id = 'form_' + (++env.counter); this.title = title; this.items = []; this.accepting = true;
    this.destination = null; this.closedMessage = ''; this.description = ''; this.confirmation = '';
  }
  getId() { return this.id; }
  getTitle() { return this.title; }
  getPublishedUrl() { return 'https://docs.google.com/forms/d/e/' + this.id + '/viewform'; }
  setDescription(d) { this.description = d; return this; }
  setConfirmationMessage(m) { this.confirmation = m; return this; }
  addTextItem() { const i = new FakeFormItem('TEXT'); this.items.push(i); return i; }
  addParagraphTextItem() { const i = new FakeFormItem('PARAGRAPH_TEXT'); this.items.push(i); return i; }
  addListItem() { const i = new FakeFormItem('LIST'); this.items.push(i); return i; }
  addMultipleChoiceItem() { const i = new FakeFormItem('MULTIPLE_CHOICE'); this.items.push(i); return i; }
  getItems(type) { return this.items.filter((i) => !type || i.type === type); }
  setAcceptingResponses(b) { this.accepting = b; return this; }
  setCustomClosedFormMessage(m) { this.closedMessage = m; return this; }
  setDestination(type, ssId) {
    if (type !== 'SPREADSHEET' || ssId !== this.env.ss.id) throw new Error('fake setDestination: unexpected ' + type + ' ' + ssId);
    const n = this.env.ss.sheets.filter((s) => /^Form Responses/.test(s.name)).length + 1;
    const sheet = this.env.ss.insertSheet('Form Responses ' + n, 0);
    sheet.formUrl = 'https://docs.google.com/forms/d/' + this.id + '/edit';
    this.destination = sheet;
    return this;
  }
  removeDestination() { if (this.destination) this.destination.formUrl = null; this.destination = null; return this; }
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

class FakeEvent {
  constructor(cal, title, start, end, allDay, opts) {
    this.cal = cal; this.id = 'ev' + (++cal.env.counter) + '@google.com'; this.title = title; this.start = start; this.end = end;
    this.allDay = allDay; this.location = (opts && opts.location) || ''; this.description = (opts && opts.description) || ''; this.deleted = false;
  }
  getId() { return this.id; }
  isAllDayEvent() { return this.allDay; }
  setTime(s, e) { this.start = s; this.end = e; return this; }
  setAllDayDate(d) { this.start = d; this.end = null; return this; }
  setTitle(t) { this.title = t; return this; }
  setLocation(l) { this.location = l; return this; }
  setDescription(d) { this.description = d; return this; }
  deleteEvent() { this.deleted = true; this.cal.events = this.cal.events.filter((e) => e !== this); }
}

class FakeCalendar {
  constructor(env, name, opts) { this.env = env; this.id = 'cal_' + (++env.counter) + '@group.calendar.google.com'; this.name = name; this.opts = opts; this.events = []; }
  getId() { return this.id; }
  getName() { return this.name; }
  createEvent(title, start, end, opts) { const e = new FakeEvent(this, title, start, end, false, opts); this.events.push(e); return e; }
  createAllDayEvent(title, date, opts) { const e = new FakeEvent(this, title, date, null, true, opts); this.events.push(e); return e; }
  getEventById(id) { return this.events.find((e) => e.id === id) || null; }
}

// ---------------------------------------------------------------------------
// Stripe (checked against Stripe's OpenAPI description when available)
// ---------------------------------------------------------------------------

function parseForm(str) {
  const out = {};
  if (!str) return out;
  str.split('&').forEach((pair) => {
    const eq = pair.indexOf('=');
    const key = decodeURIComponent(pair.slice(0, eq));
    const value = decodeURIComponent(pair.slice(eq + 1));
    const parts = key.replace(/\]/g, '').split('[');
    let o = out;
    parts.forEach((p, i) => {
      if (i === parts.length - 1) { o[p] = value; return; }
      if (o[p] === undefined) o[p] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      o = o[p];
    });
  });
  return out;
}

class FakeStripe {
  constructor(env, specPath) {
    this.env = env; this.links = {}; this.events = []; this.requests = []; this.idem = {}; this.n = 0; this.problems = [];
    this.spec = specPath && fs.existsSync(specPath) ? JSON.parse(fs.readFileSync(specPath, 'utf8')) : null;
  }
  resolve(schema) {
    while (schema && schema.$ref) {
      const name = schema.$ref.replace('#/components/schemas/', '');
      schema = this.spec.components.schemas[name];
    }
    return schema || {};
  }
  check(schema, value, where, errors) {
    schema = this.resolve(schema);
    if (schema.anyOf) {
      const ok = schema.anyOf.some((s) => { const e = []; this.check(s, value, where, e); return e.length === 0; });
      if (!ok) errors.push(where + ': does not match any allowed form');
      return;
    }
    if (Array.isArray(value)) {
      if (schema.type !== 'array') { errors.push(where + ': a list was sent, Stripe expects ' + schema.type); return; }
      value.forEach((v, i) => this.check(schema.items || {}, v, where + '[' + i + ']', errors));
      return;
    }
    if (value !== null && typeof value === 'object') {
      if (schema.type && schema.type !== 'object') { errors.push(where + ': an object was sent, Stripe expects ' + schema.type); return; }
      const props = schema.properties || {};
      Object.keys(value).forEach((k) => {
        if (k in props) this.check(props[k], value[k], where + '.' + k, errors);
        else if (schema.additionalProperties) this.check(schema.additionalProperties === true ? {} : schema.additionalProperties, value[k], where + '.' + k, errors);
        else errors.push(where + '.' + k + ': Stripe has no such parameter');
      });
      (schema.required || []).forEach((k) => { if (!(k in value)) errors.push(where + '.' + k + ': required by Stripe but missing'); });
      return;
    }
    if (schema.type === 'integer' && !/^-?\d+$/.test(value)) errors.push(where + ': expected a whole number, got "' + value + '"');
    if (schema.type === 'boolean' && !/^(true|false)$/.test(value)) errors.push(where + ': expected true/false, got "' + value + '"');
    if (schema.type === 'object') errors.push(where + ': expected an object');
    if (schema.enum && schema.enum.indexOf(value) < 0) errors.push(where + ': "' + value + '" is not one of ' + schema.enum.join(', '));
    if (schema.maxLength && String(value).length > schema.maxLength) errors.push(where + ': too long');
  }
  validate(method, specPath, params) {
    if (!this.spec) return;
    const op = this.spec.paths[specPath] && this.spec.paths[specPath][method];
    if (!op) { this.problems.push('No such Stripe endpoint: ' + method.toUpperCase() + ' ' + specPath); return; }
    const errors = [];
    if (method === 'get') {
      const allowed = {};
      (op.parameters || []).filter((p) => p.in === 'query').forEach((p) => { allowed[p.name] = p.schema; });
      Object.keys(params).forEach((k) => {
        if (!(k in allowed)) errors.push(k + ': Stripe has no such query parameter');
        else this.check(allowed[k], params[k], k, errors);
      });
    } else {
      const schema = op.requestBody.content['application/x-www-form-urlencoded'].schema;
      this.check(schema, params, specPath, errors);
    }
    errors.forEach((e) => this.problems.push(method.toUpperCase() + ' ' + specPath + ' ' + e));
  }
  respond(code, body) { return { getResponseCode: () => code, getContentText: () => JSON.stringify(body) }; }
  fetch(url, options) {
    const u = new URL(url);
    const method = (options.method || 'get').toLowerCase();
    const headers = options.headers || {};
    const params = method === 'get' ? parseForm(u.search.slice(1)) : parseForm(options.payload || '');
    this.requests.push({ method, path: u.pathname, params, headers });
    if (!/^Bearer (sk|rk)_test_/.test(headers.Authorization || '')) return this.respond(401, { error: { message: 'Invalid API key' } });
    if (!headers['Stripe-Version']) this.problems.push('Request without a Stripe-Version header');
    if (method === 'post' && options.contentType !== 'application/x-www-form-urlencoded') this.problems.push('POST without form encoding');

    let m;
    if (method === 'get' && u.pathname === '/v1/events') {
      this.validate('get', '/v1/events', params);
      const types = params.types || (params.type ? [params.type] : null);
      const gte = params.created && params.created.gte ? +params.created.gte : 0;
      let list = this.events.filter((e) => e.created >= gte && (!types || types.indexOf(e.type) >= 0)).sort((a, b) => b.created - a.created || (b.seq - a.seq));
      if (params.starting_after) { const i = list.findIndex((e) => e.id === params.starting_after); list = list.slice(i + 1); }
      const limit = +(params.limit || 10);
      return this.respond(200, { object: 'list', data: list.slice(0, limit), has_more: list.length > limit });
    }
    if (method === 'post' && u.pathname === '/v1/payment_links') {
      this.validate('post', '/v1/payment_links', params);
      const key = headers['Idempotency-Key'];
      if (key && this.idem[key]) {
        if (this.idem[key].body !== options.payload) return this.respond(400, { error: { message: 'Keys for idempotent requests can only be used with the same parameters they were first used with.' } });
        return this.respond(200, this.idem[key].result);
      }
      const id = 'plink_' + (++this.n);
      const link = { id, object: 'payment_link', active: true, url: 'https://buy.stripe.com/test_' + id, metadata: params.metadata || {}, params, completed: 0 };
      this.links[id] = link;
      const result = { id, object: 'payment_link', active: true, url: link.url, metadata: link.metadata };
      if (key) this.idem[key] = { body: options.payload, result };
      return this.respond(200, result);
    }
    if (method === 'post' && (m = u.pathname.match(/^\/v1\/payment_links\/([^/]+)$/))) {
      this.validate('post', '/v1/payment_links/{payment_link}', params);
      const link = this.links[decodeURIComponent(m[1])];
      if (!link) return this.respond(404, { error: { message: 'No such payment link' } });
      if (params.active !== undefined) link.active = params.active === 'true';
      return this.respond(200, { id: link.id, active: link.active, url: link.url });
    }
    return this.respond(404, { error: { message: 'fake Stripe: unknown request ' + method + ' ' + u.pathname } });
  }
  /** Someone pays a link (as if they filled in Stripe's checkout page). */
  pay(linkId, email, overrides) {
    const link = this.links[linkId];
    if (!link) throw new Error('fake Stripe: no link ' + linkId);
    if (!link.active) throw new Error('fake Stripe: link ' + linkId + ' is switched off');
    const amount = +link.params.line_items[0].price_data.unit_amount;
    link.completed++;
    const limit = link.params.restrictions && +link.params.restrictions.completed_sessions.limit;
    if (limit && link.completed >= limit) link.active = false;
    return this.addEvent('checkout.session.completed', Object.assign({
      id: 'cs_test_' + (++this.n), object: 'checkout.session', payment_status: 'paid', status: 'complete', amount_total: amount,
      currency: 'gbp', metadata: link.metadata, payment_link: linkId, payment_intent: 'pi_test_' + (++this.n),
      customer_details: { email }, client_reference_id: null,
    }, overrides || {}));
  }
  addEvent(type, session) {
    const ev = { id: 'evt_' + (++this.n), seq: this.n, type, created: Math.floor(Date.now() / 1000), data: { object: session } };
    this.events.push(ev);
    return ev;
  }
}

// ---------------------------------------------------------------------------
// The environment
// ---------------------------------------------------------------------------

function createEnv(options) {
  const opts = options || {};
  const env = {
    counter: 0, mail: [], quota: 100, props: {}, triggers: [], forms: {}, calendars: {}, dialogs: [], answers: [], sidebars: [],
    editors: new Set(['admin@example.org', 'treasurer@example.org']), locks: 0, menus: [],
  };
  env.ss = new FakeSpreadsheet(env);
  env.stripe = new FakeStripe(env, opts.stripeSpec);

  const Button = { OK: 'OK', CANCEL: 'CANCEL', YES: 'YES', NO: 'NO', CLOSE: 'CLOSE' };
  const ButtonSet = { OK: 'OK', OK_CANCEL: 'OK_CANCEL', YES_NO: 'YES_NO', YES_NO_CANCEL: 'YES_NO_CANCEL' };
  const nextAnswer = (kind, title, message, buttons) => {
    env.dialogs.push({ kind, title, message, buttons });
    if (env.answers.length) return env.answers.shift();
    if (kind === 'prompt') return { button: 'OK', text: '' };
    return buttons === 'OK' ? 'OK' : 'YES';
  };
  const ui = {
    Button, ButtonSet,
    alert(title, message, buttons) {
      const a = nextAnswer('alert', title, message, buttons || 'OK');
      return typeof a === 'string' ? a : a.button;
    },
    prompt(title, message, buttons) {
      const a = nextAnswer('prompt', title, message, buttons);
      const ans = typeof a === 'string' ? { button: 'OK', text: a } : a;
      return { getSelectedButton: () => ans.button, getResponseText: () => ans.text };
    },
    showSidebar(html) { env.sidebars.push(html); },
    createMenu(name) {
      const menu = { name, items: [] };
      const api = {
        addItem(label, fn) { menu.items.push([label, fn]); return api; },
        addSeparator() { menu.items.push(['---']); return api; },
        addSubMenu(sub) { menu.items.push(['>', sub.__menu]); return api; },
        addToUi() { env.menus.push(menu); },
        __menu: menu,
      };
      return api;
    },
  };

  const props = {
    getProperty: (k) => (k in env.props ? env.props[k] : null),
    setProperty: (k, v) => { env.props[k] = String(v); return props; },
    deleteProperty: (k) => { delete env.props[k]; return props; },
  };

  const globals = {
    Date,
    console,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => env.ss,
      openById: (id) => { if (id !== env.ss.id) throw new Error('fake openById: unknown ' + id); return env.ss; },
      getUi: () => { if (env.noUi) throw new Error('Cannot call SpreadsheetApp.getUi() from this context.'); return ui; },
      flush: () => {},
      newDataValidation: () => chainBuilder('dv', DV_METHODS),
      newConditionalFormatRule: () => chainBuilder('cf', CF_METHODS),
      ProtectionType: { RANGE: 'RANGE', SHEET: 'SHEET' },
    },
    Session: {
      getActiveUser: () => ({ getEmail: () => env.activeUser === undefined ? 'admin@example.org' : env.activeUser }),
      getEffectiveUser: () => ({ getEmail: () => 'admin@example.org' }),
    },
    PropertiesService: { getScriptProperties: () => props },
    LockService: {
      getScriptLock: () => ({
        held: false,
        tryLock(ms) { if (env.locks > 0) throw new Error('fake: a lock was taken while another was held (nested locking)'); env.locks++; this.held = true; return true; },
        releaseLock() { if (this.held) { env.locks--; this.held = false; } },
      }),
    },
    Utilities: {
      formatDate,
      parseDate,
      newBlob: (data, type, name) => {
        const blob = {
          data, type, name,
          getAs: (t) => Object.assign({}, blob, { type: t, setName(n) { this.name = n; return this; }, getName() { return this.name; } }),
          setName(n) { blob.name = n; return blob; },
          getName: () => blob.name,
        };
        if (env.failPdf) blob.getAs = () => { throw new Error('Conversion failed'); };
        return blob;
      },
    },
    MimeType: { HTML: 'text/html', PDF: 'application/pdf' },
    MailApp: {
      getRemainingDailyQuota: () => env.quota,
      sendEmail: (m) => {
        if (typeof m !== 'object') throw new Error('fake sendEmail: expected an object');
        const n = String(m.to).split(',').length;
        if (env.quota < n) throw new Error('Service invoked too many times for one day: email.');
        env.quota -= n;
        env.mail.push(m);
      },
    },
    UrlFetchApp: {
      fetch: (url, options) => {
        if (!/^https:\/\/api\.stripe\.com\//.test(url)) throw new Error('fake UrlFetchApp: unexpected URL ' + url);
        return env.stripe.fetch(url, options || {});
      },
    },
    ScriptApp: {
      getProjectTriggers: () => env.triggers.slice(),
      deleteTrigger: (t) => { env.triggers = env.triggers.filter((x) => x !== t); },
      newTrigger: (fn) => {
        const t = { fn, kind: null, getHandlerFunction: () => fn };
        const b = {
          timeBased: () => ({ everyMinutes: (n) => ({ create: () => { t.kind = 'every' + n + 'min'; env.triggers.push(t); return t; } }) }),
          forSpreadsheet: (ss) => ({ onFormSubmit: () => ({ create: () => { t.kind = 'formSubmit:' + ss.getId(); env.triggers.push(t); return t; } }) }),
        };
        return b;
      },
    },
    HtmlService: {
      createHtmlOutput: (html) => {
        const out = { html, title: '', setTitle(t) { this.title = t; return this; }, getContent() { return this.html; } };
        return out;
      },
    },
    FormApp: {
      create: (title) => { const f = new FakeForm(env, title); env.forms[f.id] = f; return f; },
      openById: (id) => { if (!env.forms[id]) throw new Error('No item with the given ID could be found.'); return env.forms[id]; },
      createTextValidation: () => chainBuilder('textValidation', ['setHelpText', 'requireTextIsEmail']),
      ItemType: { LIST: 'LIST', TEXT: 'TEXT', PARAGRAPH_TEXT: 'PARAGRAPH_TEXT', MULTIPLE_CHOICE: 'MULTIPLE_CHOICE' },
      DestinationType: { SPREADSHEET: 'SPREADSHEET' },
    },
    CalendarApp: {
      getCalendarById: (id) => env.calendars[id] || null,
      createCalendar: (name, o) => { const c = new FakeCalendar(env, name, o); env.calendars[c.id] = c; return c; },
    },
  };

  const context = vm.createContext(globals);
  if (opts.bundle) {
    // The single file people paste into Apps Script (dist/Club.gs).
    vm.runInContext(fs.readFileSync(opts.bundle, 'utf8'), context, { filename: path.basename(opts.bundle) });
  } else {
    const srcDir = opts.srcDir || path.join(__dirname, '..', 'src');
    fs.readdirSync(srcDir).filter((f) => f.endsWith('.js')).sort().forEach((f) => {
      vm.runInContext(fs.readFileSync(path.join(srcDir, f), 'utf8'), context, { filename: f });
    });
  }

  env.context = context;
  env.run = (code) => vm.runInContext(code, context);
  env.sheet = (name) => env.ss.getSheetByName(name);
  /** The tab as a list of {header: value} objects (formula columns are blank). */
  env.rows = (name) => {
    const t = env.run('table_(' + JSON.stringify(name) + ')');
    return t.rows.map((r) => { const o = { _row: r.n }; t.headers.forEach((h, i) => { if (h) o[h] = r.v[i]; }); return o; });
  };
  env.select = (name, rowNumbers) => {
    const sheet = env.sheet(name);
    env.ss.active = sheet;
    env.ss.selection = { sheet, ranges: rowNumbers.map((n) => sheet.getRange(n, 1, 1, 3)) };
  };
  env.lastDialog = () => env.dialogs[env.dialogs.length - 1];
  env.answer = (...answers) => { env.answers.push(...answers); };
  return env;
}

module.exports = { createEnv, formatDate, parseDate, parseForm };
