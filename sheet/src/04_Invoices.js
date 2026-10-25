/**
 * CLUB REGISTER: making invoices
 * ---------------------------------------------------------------------------
 * An invoice is one row on the Invoices tab plus one or more rows on the
 * Invoice lines tab. Its amount is worked out from its lines and what has been
 * paid from the Payments tab, by formulas anyone can see. The code below does
 * the same sums when it needs them.
 *
 * Invoices are made as drafts. Nothing is emailed until someone uses
 * Club ▸ Invoices ▸ Send draft invoices (05_Email.js).
 */

/**
 * @typedef {{description: string, code?: string, qty: number, unitPence: number, ref?: string}} DraftLine
 * @typedef {{memberId?: string, billTo: string, email?: string, type: string, year?: (number|string),
 *            description: string, dueDays?: number, lines: DraftLine[], notes?: string}} Draft
 */

/** Makes draft invoices and returns their numbers. */
function createInvoices_(drafts) {
  if (!drafts.length) return [];
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('Someone else is making invoices at this moment. Please try again in a minute.');
  try {
    const s = settings_();
    const inv = table_(TAB.INVOICES);
    const lines = table_(TAB.LINES);
    const numbers = nextInvoiceNumbers_(inv, s, drafts.length);
    const today = today_();
    const terms = Number(s['Payment terms (days)']) || 30;
    const invoiceRows = [];
    const lineRows = [];
    drafts.forEach((d, i) => {
      const no = numbers[i];
      invoiceRows.push({
        'Invoice no.': no,
        'Date': today,
        'Bill to': d.billTo,
        'Type': d.type,
        'Year': d.year || '',
        'Description': d.description,
        'Due': addDays_(today, d.dueDays || terms),
        'Member ID': d.memberId || '',
        'Email': d.email || '',
        'Stage': 'Draft',
        'Reminders': 0,
        'Notes': d.notes || '',
      });
      d.lines.forEach((l) => lineRows.push({
        'Invoice no.': no,
        'Description': l.description,
        'Code': l.code || '',
        'Qty': l.qty,
        'Unit £': pounds_(l.unitPence),
        'Ref': l.ref || '',
      }));
    });
    appendRows_(inv, invoiceRows);
    appendRows_(lines, lineRows);
    SpreadsheetApp.flush();
    return numbers;
  } finally {
    lock.releaseLock();
  }
}

/** The next free invoice numbers: one more than the highest so far, or Settings ▸ First invoice number. */
function nextInvoiceNumbers_(inv, s, count) {
  const prefix = String(s['Invoice prefix'] || '');
  let next = Math.max(Number(s['First invoice number']) || 1, 1);
  inv.rows.forEach((r) => {
    const no = String(r.get('Invoice no.')).trim();
    if (no.indexOf(prefix) !== 0) return;
    const n = Number(no.slice(prefix.length));
    if (isFinite(n) && n >= next) next = Math.floor(n) + 1;
  });
  const numbers = [];
  for (let i = 0; i < count; i++) numbers.push(prefix + (next + i));
  return numbers;
}

function linesTotal_(lines) {
  return lines.reduce((sum, l) => sum + Math.round(l.qty * l.unitPence), 0);
}

/** Every invoice's lines: {invoiceNo: [{description, qty, unitPence, linePence}]} */
function linesByInvoice_() {
  /** @type {Object<string, {description: string, qty: number, unitPence: number, linePence: number, ref: string}[]>} */
  const out = {};
  table_(TAB.LINES).rows.forEach((l) => {
    const no = String(l.get('Invoice no.')).trim();
    if (!no) return;
    const qty = Number(l.get('Qty')) || 0;
    const unitPence = pence_(l.get('Unit £'));
    (out[no] = out[no] || []).push({
      description: String(l.get('Description')), qty, unitPence, linePence: Math.round(qty * unitPence), ref: String(l.get('Ref')),
    });
  });
  return out;
}

/**
 * @typedef {{row: Row, amountPence: number, paidPence: number, balancePence: number}} InvoiceState
 */

/** Amount, paid and balance for every invoice, worked out the same way as the formulas on the Invoices tab. */
function invoiceStates_() {
  const lines = linesByInvoice_();
  /** @type {Object<string, number>} */
  const paid = {};
  table_(TAB.PAYMENTS).rows.forEach((p) => {
    const no = String(p.get('Invoice no.')).trim();
    if (no) paid[no] = (paid[no] || 0) + pence_(p.get('Amount £'));
  });
  const invoices = table_(TAB.INVOICES);
  /** @type {Object<string, InvoiceState>} */
  const states = {};
  invoices.rows.forEach((r) => {
    const no = String(r.get('Invoice no.')).trim();
    if (!no) return;
    const amountPence = (lines[no] || []).reduce((sum, l) => sum + l.linePence, 0);
    const paidPence = paid[no] || 0;
    states[no] = {
      row: r, amountPence, paidPence, balancePence: r.get('Stage') === 'Cancelled' ? 0 : amountPence - paidPence,
    };
  });
  return { invoices, states, lines };
}

/** Asks which year; returns null if they cancel. */
function askYear_(title, suggested) {
  const answer = ui_().prompt(title, 'Which year are these invoices for?\n\nPress OK for ' + suggested + ', or type another year.',
    ui_().ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui_().Button.OK) return null;
  const typed = answer.getResponseText().trim();
  const year = Number(typed || suggested);
  if (!(year >= 2000 && year <= 2100) || Math.floor(year) !== year) {
    alert_(title, '"' + typed + '" is not a year.');
    return null;
  }
  return year;
}

/** Values that appear more than once (e.g. a Member ID copied onto two rows). */
function duplicates_(values) {
  /** @type {Object<string, number>} */
  const seen = {};
  values.forEach((v) => {
    if (v) seen[v] = (seen[v] || 0) + 1;
  });
  return Object.keys(seen).filter((v) => seen[v] > 1);
}

// ---------------------------------------------------------------------------
// The yearly membership run
// ---------------------------------------------------------------------------

function prepareMembershipInvoices() {
  withUi_('Membership invoices', () => {
    const year = askYear_('Membership invoices', settings_()['Membership year']);
    if (!year) return;
    const plan = planMembershipInvoices_(year);
    const notes = plan.problems.length ? '\n\nPlease check:\n' + listText_(plan.problems) : '';
    const free = plan.free ? '\n\n' + plan.free + ' member(s) on free memberships were left out.' : '';
    if (!plan.drafts.length) {
      alert_('Membership invoices', 'Nothing to do: every Active member already has a ' + year + ' membership invoice.' + free + notes);
      return;
    }
    const total = plan.drafts.reduce((sum, d) => sum + linesTotal_(d.lines), 0);
    if (!confirm_('Membership invoices', 'Make ' + plan.drafts.length + ' draft membership invoices for ' + year + ', totalling ' +
      money_(total) + '?\n\nNothing is emailed yet. You can check them on the Invoices tab first.' + free + notes)) return;
    const numbers = createInvoices_(plan.drafts);
    log_('Membership invoices prepared', year + ': ' + numbers.length + ' drafts (' + numbers[0] + ' to ' +
      numbers[numbers.length - 1] + '), total ' + money_(total) + (plan.problems.length ? '. Check: ' + plan.problems.join(' | ') : ''));
    ss_().getSheetByName(TAB.INVOICES).activate();
    alert_('Membership invoices', 'Made ' + numbers.length + ' draft invoices (' + numbers[0] + ' to ' + numbers[numbers.length - 1] +
      ').\n\nNext: check them on the Invoices tab, then use Club ▸ Invoices ▸ Send draft invoices.');
  });
}

/** Works out who needs a membership invoice for the year. Changes nothing except filling in missing Member IDs. */
function planMembershipInvoices_(year) {
  const members = table_(TAB.MEMBERS);
  fillMissingIds_(members, 'Member ID', 'M', 4, ['First name', 'Last name']);
  const fees = feeMap_();
  const already = {};
  table_(TAB.INVOICES).rows.forEach((r) => {
    if (r.get('Type') === 'Membership' && Number(r.get('Year')) === year && r.get('Stage') !== 'Cancelled') {
      already[String(r.get('Member ID')).trim()] = true;
    }
  });
  const dupes = duplicates_(members.rows.map((r) => String(r.get('Member ID')).trim()));
  /** @type {Draft[]} */
  const drafts = [];
  const problems = [];
  let free = 0;
  members.rows.forEach((r) => {
    if (String(r.get('Status')).trim() !== 'Active') return;
    const id = String(r.get('Member ID')).trim();
    const name = fullName_(r) || '(no name)';
    if (dupes.indexOf(id) >= 0) {
      problems.push(name + ': Member ID ' + id + ' is used on more than one row, so nobody with it was invoiced');
      return;
    }
    if (already[id]) return;
    const code = String(r.get('Membership')).trim();
    const fee = fees[code];
    if (!fee || fee.category !== 'Membership') {
      problems.push(name + ' (' + id + '): no membership price on the Fees tab for "' + code + '"');
      return;
    }
    if (fee.pence <= 0) {
      free++;
      return;
    }
    const email = String(r.get('Email')).trim();
    if (!isEmail_(email)) problems.push(name + ' (' + id + '): no email address, so the invoice can\'t be emailed');
    const description = fee.description + ' ' + year;
    drafts.push({
      memberId: id, billTo: name, email, type: 'Membership', year, description,
      lines: [{ description, code, qty: 1, unitPence: fee.pence, ref: id }],
    });
  });
  return { drafts, problems, free };
}

// ---------------------------------------------------------------------------
// The yearly boat park run
// ---------------------------------------------------------------------------

function prepareBoatParkInvoices() {
  withUi_('Boat park invoices', () => {
    const year = askYear_('Boat park invoices', settings_()['Membership year']);
    if (!year) return;
    const plan = planBoatParkInvoices_(year);
    const notes = plan.problems.length ? '\n\nPlease check:\n' + listText_(plan.problems) : '';
    if (!plan.drafts.length) {
      alert_('Boat park invoices', 'Nothing to do: every boat In park has already been charged for ' + year + '.' + notes);
      return;
    }
    const boats = plan.drafts.reduce((sum, d) => sum + d.lines.length, 0);
    const total = plan.drafts.reduce((sum, d) => sum + linesTotal_(d.lines), 0);
    if (!confirm_('Boat park invoices', 'Make ' + plan.drafts.length + ' draft boat park invoices for ' + year + ' (' + boats +
      ' boats), totalling ' + money_(total) + '?\n\nOwners with more than one boat get one invoice. Nothing is emailed yet.' + notes)) return;
    const numbers = createInvoices_(plan.drafts);
    log_('Boat park invoices prepared', year + ': ' + numbers.length + ' drafts for ' + boats + ' boats (' + numbers[0] + ' to ' +
      numbers[numbers.length - 1] + '), total ' + money_(total) + (plan.problems.length ? '. Check: ' + plan.problems.join(' | ') : ''));
    ss_().getSheetByName(TAB.INVOICES).activate();
    alert_('Boat park invoices', 'Made ' + numbers.length + ' draft invoices (' + numbers[0] + ' to ' + numbers[numbers.length - 1] +
      ').\n\nNext: check them on the Invoices tab, then use Club ▸ Invoices ▸ Send draft invoices.');
  });
}

/** Works out which boats need charging for the year, grouped by owner. Changes nothing except filling in missing Boat IDs. */
function planBoatParkInvoices_(year) {
  const boats = table_(TAB.BOATS);
  fillMissingIds_(boats, 'Boat ID', 'B', 4, ['Boat name', 'Class', 'Sail number', 'Owner']);
  const members = memberMap_();
  const fees = feeMap_();

  // Boats already on a boat park invoice for this year (cancelled invoices don't count).
  const invoiceInfo = {};
  table_(TAB.INVOICES).rows.forEach((r) => {
    invoiceInfo[String(r.get('Invoice no.')).trim()] = r.get('Type') === 'Boat park' && Number(r.get('Year')) === year &&
      r.get('Stage') !== 'Cancelled';
  });
  const charged = {};
  table_(TAB.LINES).rows.forEach((l) => {
    if (invoiceInfo[String(l.get('Invoice no.')).trim()] && String(l.get('Ref')).trim()) charged[String(l.get('Ref')).trim()] = true;
  });

  const dupes = duplicates_(boats.rows.map((r) => String(r.get('Boat ID')).trim()));
  /** @type {Object<string, {owner: {id: string, name: string, email: string, status: string}, lines: DraftLine[]}>} */
  const byOwner = {};
  const order = [];
  const problems = [];
  boats.rows.forEach((b) => {
    if (String(b.get('Status')).trim() !== 'In park') return;
    const id = String(b.get('Boat ID')).trim();
    const label = boatLabel_(b);
    if (dupes.indexOf(id) >= 0) {
      problems.push(label + ': Boat ID ' + id + ' is used on more than one row, so it was not charged');
      return;
    }
    if (charged[id]) return;
    const code = String(b.get('Boat park fee')).trim();
    const fee = fees[code];
    if (!fee || fee.category !== 'Boat park') {
      problems.push(label + ': no boat park price on the Fees tab for "' + code + '"');
      return;
    }
    const ownerText = String(b.get('Owner')).trim();
    const owner = members[idFromLabel_(ownerText)];
    if (!owner) {
      problems.push(label + ': ' + (ownerText ? 'owner "' + ownerText + '" is not on the Members tab' : 'no owner'));
      return;
    }
    if (owner.status === 'Left' || owner.status === 'Lapsed') {
      problems.push(label + ': the owner, ' + owner.name + ', is marked ' + owner.status + ' (charged anyway, as the boat is In park)');
    }
    if (!byOwner[owner.id]) {
      byOwner[owner.id] = { owner, lines: [] };
      order.push(owner.id);
    }
    byOwner[owner.id].lines.push({
      description: fee.description + ' ' + year + ': ' + label, code, qty: 1, unitPence: fee.pence, ref: id,
    });
  });

  const drafts = order.map((ownerId) => {
    const g = byOwner[ownerId];
    if (!isEmail_(g.owner.email)) problems.push(g.owner.name + ' (' + g.owner.id + '): no email address, so the invoice can\'t be emailed');
    return {
      memberId: g.owner.id, billTo: g.owner.name, email: g.owner.email, type: 'Boat park', year,
      description: 'Boat park ' + year + (g.lines.length > 1 ? ' (' + g.lines.length + ' boats)' : ''), lines: g.lines,
    };
  });
  return { drafts, problems };
}

/**
 * "Wild Goose (Wayfarer 10234), space C7"
 * @param {Row} b
 */
function boatLabel_(b) {
  const name = String(b.get('Boat name')).trim() || 'Unnamed boat';
  const what = [String(b.get('Class')).trim(), String(b.get('Sail number')).trim()].filter(String).join(' ');
  const space = String(b.get('Space')).trim();
  return name + (what ? ' (' + what + ')' : '') + (space ? ', space ' + space : '');
}

// ---------------------------------------------------------------------------
// The invoice builder (the sidebar is in 09_Builder.js)
// ---------------------------------------------------------------------------

function openInvoiceBuilder() {
  withUi_('New invoice', () => {
    ui_().showSidebar(HtmlService.createHtmlOutput(builderHtml_()).setTitle('New invoice'));
  });
}

/** What the sidebar needs: members, fees and defaults. Called from the sidebar. */
function builderData() {
  const s = settings_();
  const members = table_(TAB.MEMBERS).rows
    .filter((r) => String(r.get('Member ID')).trim() && String(r.get('Status')).trim() !== 'Left')
    .map((r) => ({ id: String(r.get('Member ID')).trim(), name: fullName_(r), email: String(r.get('Email')).trim() }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const fees = feeMap_();
  return {
    members,
    fees: Object.keys(fees).map((code) => ({ code, description: fees[code].description, pence: fees[code].pence, category: fees[code].category })),
    types: LISTS.invoiceType,
    year: Number(s['Membership year']) || new Date().getFullYear(),
    dueDays: Number(s['Payment terms (days)']) || 30,
    testMode: yes_(s['Test mode']),
    testEmail: String(s['Test email address'] || ''),
    stripe: !!stripeKey_(),
  };
}

/**
 * Makes an invoice from the sidebar. Called from the sidebar.
 * @param {{memberId?: string, billTo?: string, email?: string, type: string, year?: (number|string),
 *          description?: string, dueDays?: (number|string), send?: boolean,
 *          lines: {code?: string, description: string, qty: (number|string), unit: (number|string)}[]}} p
 */
function builderCreate(p) {
  if (!p || !Array.isArray(p.lines) || !p.lines.length) throw new Error('Add at least one line.');
  const memberId = String(p.memberId || '').trim();
  let billTo = safe_(String(p.billTo || '').trim());
  let email = String(p.email || '').trim();
  if (memberId) {
    const m = memberMap_()[memberId];
    if (!m) throw new Error('Member ' + memberId + ' is not on the Members tab.');
    billTo = billTo || m.name;
    email = email || m.email;
  }
  if (!billTo) throw new Error('Who is the invoice for?');
  if (email && !isEmail_(email)) throw new Error('"' + email + '" does not look like an email address.');
  if (LISTS.invoiceType.indexOf(p.type) < 0) throw new Error('Choose what kind of invoice this is.');
  /** @type {DraftLine[]} */
  const lines = p.lines.map((l, i) => {
    const description = safe_(String(l.description || '').trim());
    const qty = Number(l.qty);
    const unit = Number(l.unit);
    if (!description) throw new Error('Line ' + (i + 1) + ' needs a description.');
    if (!(qty > 0) || Math.floor(qty) !== qty) throw new Error('Line ' + (i + 1) + ': the quantity must be a whole number, 1 or more.');
    if (!isFinite(unit) || String(l.unit).trim() === '') throw new Error('Line ' + (i + 1) + ': the price must be a number of pounds.');
    return { description, code: String(l.code || ''), qty, unitPence: pence_(unit), ref: memberId };
  });
  const total = linesTotal_(lines);
  if (total <= 0) throw new Error('The total must be more than £0.');
  const year = Number(p.year) || '';
  const dueDays = Math.max(Number(p.dueDays) || 0, 0);
  const no = createInvoices_([{
    memberId, billTo, email, type: p.type, year, description: safe_(String(p.description || '').trim()) || lines[0].description,
    dueDays: dueDays || undefined, lines,
  }])[0];
  log_('Invoice made with New invoice', no + ' for ' + billTo + ', ' + money_(total));
  if (!p.send) return { invoiceNo: no, total: money_(total), sent: false, message: 'Saved as a draft.' };
  if (!email) return { invoiceNo: no, total: money_(total), sent: false, message: 'Saved as a draft: there is no email address to send it to.' };
  const result = sendInvoices_([no]);
  return { invoiceNo: no, total: money_(total), sent: result.sent === 1, message: sendSummary_(result) };
}

// ---------------------------------------------------------------------------
// Cancelling
// ---------------------------------------------------------------------------

function cancelSelectedInvoice() {
  withUi_('Cancel invoice', () => {
    const selected = selectedRowNumbers_(TAB.INVOICES);
    if (selected.length !== 1) throw new Error('Click on one invoice (one row) on the Invoices tab first.');
    const all = invoiceStates_();
    const r = all.invoices.rows.filter((x) => x.n === selected[0])[0];
    if (!r) throw new Error('That row is empty.');
    const no = String(r.get('Invoice no.')).trim();
    const st = all.states[no];
    if (r.get('Stage') === 'Cancelled') {
      alert_('Cancel invoice', 'Invoice ' + no + ' is already cancelled.');
      return;
    }
    if (st.paidPence !== 0) {
      throw new Error('Invoice ' + no + ' has payments of ' + money_(st.paidPence) + ' against it. Refund them first ' +
        '(in Stripe, then add the refund on the Payments tab as a minus amount), then cancel it.');
    }
    const wasSent = r.get('Stage') === 'Sent';
    if (!confirm_('Cancel invoice', 'Cancel invoice ' + no + ' for ' + r.get('Bill to') + ' (' + money_(st.amountPence) + ')?\n\n' +
      'It stays on the Invoices tab marked Cancelled, and its card payment link stops working.' +
      (wasSent ? '\n\nIt has already been emailed, so let ' + r.get('Bill to') + ' know.' : ''))) return;
    closePayLink_(r);
    r.set('Stage', 'Cancelled');
    r.set('Notes', addNote_(r.get('Notes'), 'Cancelled ' + longDate_(today_()) + ' by ' + who_()));
    log_('Invoice cancelled', no + ' for ' + r.get('Bill to') + ', ' + money_(st.amountPence));
    alert_('Cancel invoice', 'Invoice ' + no + ' is cancelled.');
  });
}
