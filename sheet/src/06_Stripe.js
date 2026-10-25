/**
 * CLUB REGISTER: Stripe card payments
 * ---------------------------------------------------------------------------
 * Each emailed invoice gets its own Stripe payment link for the amount owed.
 * Every 15 minutes (and whenever someone uses Club ▸ Payments ▸ Update
 * payments now) the register asks Stripe which links have been paid and
 * records those payments on the Payments tab.
 *
 * Why ask Stripe rather than have Stripe tell us (a "webhook")? Google Apps
 * Script cannot check that a webhook really came from Stripe, and it answers
 * Stripe in a way Stripe counts as a failure. Asking needs no public web
 * address at all, and nothing is missed if the check is late.
 *
 * The Stripe key is kept in the script's properties, not in the sheet. Use a
 * restricted key with only the permissions listed in connectStripe() below.
 */

const STRIPE_API_VERSION = '2024-06-20';
const STRIPE_EVENTS = ['checkout.session.completed', 'checkout.session.async_payment_succeeded'];

function stripeKey_() {
  return PropertiesService.getScriptProperties().getProperty('STRIPE_SECRET_KEY') || '';
}

function connectStripe() {
  withUi_('Connect Stripe', () => {
    const answer = ui_().prompt('Connect Stripe',
      'Paste the Stripe key for this register.\n\n' +
      'Best: a restricted key (starts rk_) with only these permissions: Payment Links: Write, Products: Write, ' +
      'Prices: Write, Checkout Sessions: Read, Events: Read.\n\n' +
      'A test key (rk_test_ or sk_test_) lets you try everything without real money.\n\n' +
      'Leave it empty and press OK to disconnect Stripe.', ui_().ButtonSet.OK_CANCEL);
    if (answer.getSelectedButton() !== ui_().Button.OK) return;
    const key = answer.getResponseText().trim();
    const props = PropertiesService.getScriptProperties();
    if (!key) {
      props.deleteProperty('STRIPE_SECRET_KEY');
      setSetting_('Stripe', 'Not connected');
      log_('Stripe disconnected', '');
      alert_('Connect Stripe', 'Stripe is disconnected. New invoices will not have a pay-by-card button.');
      return;
    }
    if (!/^(sk|rk)_(test|live)_[A-Za-z0-9]+$/.test(key)) throw new Error('That does not look like a Stripe secret or restricted key.');
    stripeRequest_(key, 'get', 'events', { limit: 1 }); // checks the key works and can read payments
    props.setProperty('STRIPE_SECRET_KEY', key);
    if (!props.getProperty('STRIPE_LAST_EVENT')) {
      props.setProperty('STRIPE_LAST_EVENT', String(Math.floor(Date.now() / 1000) - 7 * 86400));
    }
    const mode = key.indexOf('_test_') > 0 ? 'test mode (no real money)' : 'LIVE (real money)';
    setSetting_('Stripe', 'Connected, ' + mode);
    log_('Stripe connected', mode);
    alert_('Connect Stripe', 'Stripe is connected in ' + mode + '.\n\nNext: Club ▸ Admin ▸ Turn on automatic payment checks.');
  });
}

function turnOnPaymentChecks() {
  withUi_('Automatic payment checks', () => {
    if (!stripeKey_()) throw new Error('Connect Stripe first: Club ▸ Admin ▸ Connect Stripe.');
    ScriptApp.getProjectTriggers()
      .filter((t) => t.getHandlerFunction() === 'checkStripePayments')
      .forEach((t) => ScriptApp.deleteTrigger(t));
    ScriptApp.newTrigger('checkStripePayments').timeBased().everyMinutes(15).create();
    log_('Automatic payment checks turned on', 'Every 15 minutes, running as ' + who_());
    alert_('Automatic payment checks', 'Done. Every 15 minutes the register asks Stripe for new card payments and puts them on the ' +
      'Payments tab.\n\nThe checks run as ' + who_() + '. If that account is ever closed, turn them on again from another Admin account.');
  });
}

/** Menu item: check now rather than waiting for the next automatic check. */
function updatePaymentsNow() {
  withUi_('Update payments', () => {
    const r = runPaymentCheck_();
    const parts = [];
    if (!r.stripe) parts.push('Stripe is not connected, so only bank payments typed on the Payments tab were looked at.');
    parts.push(r.recorded.length ? 'New card payments recorded:\n' + listText_(r.recorded) : 'No new card payments.');
    if (r.unmatched.length) parts.push('Card payments that could not be matched to an invoice (please fill in the invoice number on the Payments tab):\n' + listText_(r.unmatched));
    if (r.activated.length) parts.push('New members now Active:\n' + listText_(r.activated));
    if (r.closedLinks) parts.push(r.closedLinks + ' pay link(s) for fully paid invoices were switched off.');
    alert_('Update payments', parts.join('\n\n'));
  });
}

/** The automatic check (run every 15 minutes by a trigger). */
function checkStripePayments() {
  runPaymentCheck_();
}

function runPaymentCheck_() {
  const result = { stripe: !!stripeKey_(), recorded: /** @type {string[]} */ ([]), unmatched: /** @type {string[]} */ ([]),
    activated: /** @type {string[]} */ ([]), closedLinks: 0 };
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return result;
  try {
    if (result.stripe) recordStripePayments_(result);
    tidyAfterPayments_(result);
    setSetting_('Last payment check', new Date());
  } finally {
    lock.releaseLock();
  }
  if (result.recorded.length || result.unmatched.length || result.activated.length) {
    log_('Payments updated', [
      result.recorded.length ? 'Card payments: ' + result.recorded.join(', ') : '',
      result.unmatched.length ? 'Unmatched: ' + result.unmatched.join(', ') : '',
      result.activated.length ? 'Now Active: ' + result.activated.join(', ') : '',
    ].filter(String).join('. '));
  }
  return result;
}

/** Asks Stripe for payments since the last check and adds any new ones to the Payments tab. */
function recordStripePayments_(result) {
  const props = PropertiesService.getScriptProperties();
  const since = Number(props.getProperty('STRIPE_LAST_EVENT')) || Math.floor(Date.now() / 1000) - 7 * 86400;
  const events = [];
  let after = '';
  for (let page = 0; page < 20; page++) {
    /** @type {Object<string, any>} */
    const params = { types: STRIPE_EVENTS, created: { gte: since - 600 }, limit: 100 };
    if (after) params.starting_after = after;
    const list = stripe_('get', 'events', params);
    events.push.apply(events, list.data || []);
    if (!list.has_more || !list.data || !list.data.length) break;
    after = list.data[list.data.length - 1].id;
  }
  events.sort((a, b) => a.created - b.created);

  const pay = table_(TAB.PAYMENTS);
  const seen = {};
  pay.rows.forEach((r) => {
    const id = String(r.get('Stripe ID')).trim();
    if (id) seen[id] = true;
  });
  const known = {};
  const byLink = {};
  table_(TAB.INVOICES).rows.forEach((r) => {
    const no = String(r.get('Invoice no.')).trim();
    known[no] = true;
    const link = String(r.get('Stripe link ID')).trim();
    if (link) byLink[link] = no;
  });

  const rows = [];
  let newest = since;
  events.forEach((ev) => {
    newest = Math.max(newest, ev.created);
    const session = ev.data && ev.data.object;
    if (!session || session.payment_status !== 'paid') return; // bank-based methods finish later, in a second event
    const stripeId = String(session.payment_intent || session.id);
    if (seen[stripeId]) return;
    seen[stripeId] = true;
    let no = String((session.metadata && session.metadata.invoice_no) || byLink[session.payment_link] || '');
    if (no && !known[no]) no = '';
    const amount = Number(session.amount_total) || 0;
    const email = (session.customer_details && session.customer_details.email) || '';
    const currency = String(session.currency || 'gbp').toLowerCase();
    rows.push({
      'Date': dateOnly_(new Date(ev.created * 1000)),
      'Invoice no.': no,
      'Amount £': pounds_(amount),
      'Method': 'Card (Stripe)',
      'Reference': safe_(email),
      'Recorded by': 'Stripe (automatic)',
      'Stripe ID': stripeId,
      'Notes': (no ? '' : 'Not matched to an invoice: please fill in the invoice number. ') +
        (currency !== 'gbp' ? 'Paid in ' + currency.toUpperCase() + '.' : ''),
    });
    (no ? result.recorded : result.unmatched).push((no || email || stripeId) + ' ' + money_(amount));
  });
  appendRows_(pay, rows);
  props.setProperty('STRIPE_LAST_EVENT', String(newest));
}

/**
 * After payments (card or bank): switch off pay links for invoices that are
 * fully paid, and make new members Active once their first invoice is paid.
 */
function tidyAfterPayments_(result) {
  const all = invoiceStates_();
  const members = memberMap_();
  all.invoices.rows.forEach((r) => {
    const st = all.states[String(r.get('Invoice no.')).trim()];
    if (!st || r.get('Stage') !== 'Sent' || st.amountPence <= 0 || st.balancePence > 0) return;
    if (String(r.get('Pay link')).trim()) {
      closePayLink_(r);
      result.closedLinks++;
    }
    if (r.get('Type') === 'Membership') {
      const m = members[String(r.get('Member ID')).trim()];
      if (m && m.status === 'Applicant') {
        m.row.set('Status', 'Active');
        if (!(m.row.get('Joined') instanceof Date)) m.row.set('Joined', today_());
        m.status = 'Active';
        result.activated.push(m.name + ' (' + m.id + ')');
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Payment links
// ---------------------------------------------------------------------------

/**
 * The card payment link for an invoice, for the amount now owed. Reuses the
 * existing link if the amount hasn't changed; otherwise switches the old one
 * off and makes a new one. Without Stripe, returns any still-valid link or ''.
 */
/**
 * @param {Row} r
 * @param {number} pence
 * @param {string} email
 * @param {Object<string, any>} s
 * @returns {string}
 */
function ensurePayLink_(r, pence, email, s) {
  const existing = String(r.get('Pay link')).trim();
  if (existing && pence_(r.get('Link £')) === pence) return existing;
  if (!stripeKey_() || pence <= 0) return '';
  const previous = String(r.get('Stripe link ID')).trim();
  if (existing) closePayLink_(r);
  const no = String(r.get('Invoice no.')).trim();
  const club = String(s['Club name'] || 'Club');
  const link = stripe_('post', 'payment_links', {
    line_items: [{
      quantity: 1,
      price_data: {
        currency: 'gbp',
        unit_amount: pence,
        product_data: { name: club + ' invoice ' + no, description: String(r.get('Description')).trim() || 'Invoice ' + no },
      },
    }],
    metadata: { invoice_no: no },
    payment_intent_data: { metadata: { invoice_no: no }, description: club + ' invoice ' + no },
    restrictions: { completed_sessions: { limit: 1 } },
    after_completion: {
      type: 'hosted_confirmation',
      hosted_confirmation: { custom_message: 'Thank you. Your payment for invoice ' + no + ' has been received.' },
    },
  }, 'paylink-' + no + '-' + pence + '-' + (previous || 'first'));
  let url = String(link.url) + '?client_reference_id=' + encodeURIComponent(no.replace(/[^A-Za-z0-9_-]/g, '_'));
  if (isEmail_(email)) url += '&prefilled_email=' + encodeURIComponent(email);
  r.set('Pay link', url);
  r.set('Link £', pounds_(pence));
  r.set('Stripe link ID', String(link.id));
  return url;
}

/**
 * Switches off an invoice's pay link (it stays recorded, so late payments still match).
 * @param {Row} r
 */
function closePayLink_(r) {
  const id = String(r.get('Stripe link ID')).trim();
  if (id && stripeKey_()) {
    try {
      stripe_('post', 'payment_links/' + encodeURIComponent(id), { active: false });
    } catch (e) {
      log_('Could not switch off a pay link', id + ': ' + errMsg_(e));
    }
  }
  if (String(r.get('Pay link')).trim()) {
    r.set('Pay link', '');
    r.set('Link £', '');
  }
}

// ---------------------------------------------------------------------------
// Talking to Stripe
// ---------------------------------------------------------------------------

function stripe_(method, path, params, idempotencyKey) {
  const key = stripeKey_();
  if (!key) throw new Error('Stripe is not connected (Club ▸ Admin ▸ Connect Stripe).');
  return stripeRequest_(key, method, path, params, idempotencyKey);
}

/**
 * @param {string} key
 * @param {'get'|'post'} method
 * @param {string} path
 * @param {Object<string, any>=} params
 * @param {string=} idempotencyKey
 */
function stripeRequest_(key, method, path, params, idempotencyKey) {
  /** @type {GoogleAppsScript.URL_Fetch.HttpHeaders} */
  const headers = { Authorization: 'Bearer ' + key, 'Stripe-Version': STRIPE_API_VERSION };
  /** @type {GoogleAppsScript.URL_Fetch.URLFetchRequestOptions} */
  const options = { method, headers, muteHttpExceptions: true };
  let url = 'https://api.stripe.com/v1/' + path;
  const body = params ? formEncode_(params, '') : '';
  if (method === 'get') {
    if (body) url += '?' + body;
  } else {
    options.payload = body;
    options.contentType = 'application/x-www-form-urlencoded';
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  }
  const response = UrlFetchApp.fetch(url, options);
  const code = response.getResponseCode();
  let data = {};
  try {
    data = JSON.parse(response.getContentText());
  } catch (e) {
    data = {};
  }
  if (code < 200 || code >= 300) {
    const error = /** @type {{error?: {message?: string}}} */ (data).error;
    throw new Error((error && error.message) || 'Stripe replied with error ' + code);
  }
  return /** @type {any} */ (data);
}

/** Turns {a: {b: 1}, c: [x, y]} into a=…&a[b]=1&c[0]=x&c[1]=y, the way Stripe expects. */
function formEncode_(value, prefix) {
  /** @type {string[]} */
  const parts = [];
  if (Array.isArray(value)) {
    value.forEach((item, i) => parts.push(formEncode_(item, prefix + '[' + i + ']')));
  } else if (value !== null && typeof value === 'object') {
    Object.keys(value).forEach((k) => {
      if (value[k] === undefined || value[k] === null) return;
      parts.push(formEncode_(value[k], prefix ? prefix + '[' + k + ']' : k));
    });
  } else {
    return encodeURIComponent(prefix) + '=' + encodeURIComponent(String(value));
  }
  return parts.filter(String).join('&');
}
