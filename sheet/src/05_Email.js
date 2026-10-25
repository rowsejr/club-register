/**
 * CLUB REGISTER: emailing invoices and reminders
 * ---------------------------------------------------------------------------
 * Emails go out from the Google account that runs the menu item. An ordinary
 * Gmail account can send to about 100 people a day, so long runs stop when
 * the day's allowance is used up and the rest wait as drafts for tomorrow.
 *
 * While Settings ▸ Test mode is Yes, every email goes to the test address.
 */

function sendDraftInvoices() {
  withUi_('Send invoices', () => {
    const all = invoiceStates_();
    const drafts = all.invoices.rows.filter((r) => r.get('Stage') === 'Draft');
    if (!drafts.length) {
      alert_('Send invoices', 'There are no draft invoices to send.');
      return;
    }
    let chosen = drafts;
    const onInvoices = ss_().getActiveSheet().getName() === TAB.INVOICES;
    const selected = onInvoices ? selectedRowNumbers_(TAB.INVOICES) : [];
    const selectedDrafts = drafts.filter((r) => selected.indexOf(r.n) >= 0);
    if (selectedDrafts.length && selectedDrafts.length < drafts.length) {
      const answer = ui_().alert('Send invoices', 'You have selected ' + selectedDrafts.length + ' draft invoice(s).\n\n' +
        'Yes: send just the selected one(s).\nNo: send all ' + drafts.length + ' drafts.', ui_().ButtonSet.YES_NO_CANCEL);
      if (answer !== ui_().Button.YES && answer !== ui_().Button.NO) return;
      if (answer === ui_().Button.YES) chosen = selectedDrafts;
    }

    const s = settings_();
    const total = chosen.reduce((sum, r) => sum + all.states[String(r.get('Invoice no.')).trim()].amountPence, 0);
    const allowance = Math.max(MailApp.getRemainingDailyQuota() - EMAIL_SPARE, 0);
    let message = 'Email ' + chosen.length + ' invoice(s), totalling ' + money_(total) + '?\n\nThey will be sent from ' +
      Session.getEffectiveUser().getEmail() + ' (the account you are using now).';
    if (yes_(s['Test mode'])) message += '\n\nTEST MODE is on: every email goes to ' + s['Test email address'] + ', not to members.';
    if (!stripeKey_()) message += '\n\nStripe is not connected, so the emails will not have a pay-by-card button.';
    if (chosen.length > allowance) {
      message += '\n\nGmail will let this account send ' + allowance + ' more email(s) today. The rest will stay as drafts: ' +
        'use Send draft invoices again tomorrow to finish.';
    }
    if (!confirm_('Send invoices', message)) return;
    const result = sendInvoices_(chosen.map((r) => String(r.get('Invoice no.')).trim()));
    alert_('Send invoices', sendSummary_(result));
  });
}

/**
 * Emails draft invoices. Each one is marked Sent as soon as its email has
 * gone, so if anything stops part-way, running it again carries on where it
 * left off and nobody gets two copies.
 */
function sendInvoices_(numbers) {
  const result = { sent: 0, sentNos: /** @type {string[]} */ ([]), skipped: /** @type {string[]} */ ([]),
    errors: /** @type {string[]} */ ([]), left: /** @type {string[]} */ ([]) };
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('Invoices are being sent by someone else at this moment. Please try again in a few minutes.');
  try {
    const started = Date.now();
    const s = settings_();
    const all = invoiceStates_();
    let allowance = MailApp.getRemainingDailyQuota() - EMAIL_SPARE;
    numbers.forEach((no) => {
      const st = all.states[no];
      if (!st || st.row.get('Stage') !== 'Draft') return;
      if (allowance <= 0 || Date.now() - started > TIME_BUDGET_MS) {
        result.left.push(no);
        return;
      }
      const r = st.row;
      const email = String(r.get('Email')).trim();
      if (!isEmail_(email)) {
        result.skipped.push(no + ' (' + r.get('Bill to') + '): no email address');
        return;
      }
      if (st.amountPence <= 0) {
        result.skipped.push(no + ' (' + r.get('Bill to') + '): the total is £0');
        return;
      }
      let link = '';
      try {
        link = ensurePayLink_(r, st.balancePence, email, s);
      } catch (e) {
        result.errors.push(no + ': Stripe said "' + errMsg_(e) + '"');
        return;
      }
      try {
        const msg = invoiceMessage_(r, all.lines[no] || [], st, link, s, false);
        deliver_(email, msg.subject, msg.html, msg.text, s, yes_(s['Attach PDF invoice']) ? pdfOf_(msg.printHtml, no) : []);
        allowance--;
      } catch (e) {
        result.errors.push(no + ': ' + errMsg_(e));
        return;
      }
      r.set('Stage', 'Sent');
      r.set('Sent on', today_());
      result.sent++;
      result.sentNos.push(no);
    });
  } finally {
    lock.releaseLock();
  }
  if (result.sent || result.errors.length || result.skipped.length) {
    log_('Invoices sent', result.sent + ' sent' + (result.sentNos.length ? ' (' + result.sentNos.join(', ') + ')' : '') +
      (result.left.length ? '; ' + result.left.length + ' left for tomorrow' : '') +
      (result.skipped.length ? '; not sent: ' + result.skipped.join(' | ') : '') +
      (result.errors.length ? '; problems: ' + result.errors.join(' | ') : ''));
  }
  return result;
}

function sendSummary_(r) {
  const parts = [r.sent ? 'Emailed ' + r.sent + ' invoice(s).' : 'No invoices were emailed.'];
  if (r.left.length) {
    parts.push(r.left.length + ' are still drafts because Gmail\'s daily limit (or Google\'s time limit) was reached. ' +
      'Use Send draft invoices again tomorrow to send them.');
  }
  if (r.skipped.length) parts.push('Not sent:\n' + listText_(r.skipped));
  if (r.errors.length) parts.push('Problems:\n' + listText_(r.errors));
  return parts.join('\n\n');
}

// ---------------------------------------------------------------------------
// Reminders
// ---------------------------------------------------------------------------

function sendReminders() {
  withUi_('Send reminders', () => {
    const s = settings_();
    const gap = Number(s['Days between reminders']) || 14;
    const today = today_();
    const all = invoiceStates_();
    const overdue = all.invoices.rows.filter((r) => {
      const st = all.states[String(r.get('Invoice no.')).trim()];
      if (!st || r.get('Stage') !== 'Sent' || st.balancePence <= 0) return false;
      const due = r.get('Due');
      if (!(due instanceof Date) || daysBetween_(due, today) <= 0) return false;
      const last = r.get('Last reminder');
      return !(last instanceof Date && daysBetween_(last, today) < gap);
    });
    if (!overdue.length) {
      alert_('Send reminders', 'Nobody needs a reminder today. Either nothing is overdue, or everyone overdue was reminded in the last ' +
        gap + ' days.');
      return;
    }
    const total = overdue.reduce((sum, r) => sum + all.states[String(r.get('Invoice no.')).trim()].balancePence, 0);
    let message = 'Send reminders for ' + overdue.length + ' overdue invoice(s), ' + money_(total) + ' in total?\n\nThey will be sent from ' +
      Session.getEffectiveUser().getEmail() + '.';
    if (yes_(s['Test mode'])) message += '\n\nTEST MODE is on: every email goes to ' + s['Test email address'] + '.';
    const allowance = Math.max(MailApp.getRemainingDailyQuota() - EMAIL_SPARE, 0);
    if (overdue.length > allowance) message += '\n\nGmail will only let this account send ' + allowance + ' more today; the rest can go tomorrow.';
    if (!confirm_('Send reminders', message)) return;
    const result = remind_(overdue.map((r) => String(r.get('Invoice no.')).trim()));
    const parts = [result.sent ? 'Sent ' + result.sent + ' reminder(s).' : 'No reminders were sent.'];
    if (result.left.length) parts.push(result.left.length + ' could not go today because of Gmail\'s daily limit. Send reminders again tomorrow.');
    if (result.skipped.length) parts.push('Not sent:\n' + listText_(result.skipped));
    if (result.errors.length) parts.push('Problems:\n' + listText_(result.errors));
    alert_('Send reminders', parts.join('\n\n'));
  });
}

function remind_(numbers) {
  const result = { sent: 0, skipped: /** @type {string[]} */ ([]), errors: /** @type {string[]} */ ([]), left: /** @type {string[]} */ ([]) };
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('Someone else is sending emails at this moment. Please try again in a few minutes.');
  const sentNos = [];
  try {
    const started = Date.now();
    const s = settings_();
    const all = invoiceStates_();
    let allowance = MailApp.getRemainingDailyQuota() - EMAIL_SPARE;
    numbers.forEach((no) => {
      const st = all.states[no];
      if (!st) return;
      if (allowance <= 0 || Date.now() - started > TIME_BUDGET_MS) {
        result.left.push(no);
        return;
      }
      const r = st.row;
      const email = String(r.get('Email')).trim();
      if (!isEmail_(email)) {
        result.skipped.push(no + ' (' + r.get('Bill to') + '): no email address');
        return;
      }
      try {
        const link = ensurePayLink_(r, st.balancePence, email, s);
        const msg = invoiceMessage_(r, all.lines[no] || [], st, link, s, true);
        deliver_(email, msg.subject, msg.html, msg.text, s, []);
        allowance--;
      } catch (e) {
        result.errors.push(no + ': ' + errMsg_(e));
        return;
      }
      r.set('Reminders', (Number(r.get('Reminders')) || 0) + 1);
      r.set('Last reminder', today_());
      result.sent++;
      sentNos.push(no);
    });
  } finally {
    lock.releaseLock();
  }
  if (result.sent || result.errors.length) {
    log_('Reminders sent', result.sent + ' sent' + (sentNos.length ? ' (' + sentNos.join(', ') + ')' : '') +
      (result.errors.length ? '; problems: ' + result.errors.join(' | ') : ''));
  }
  return result;
}

// ---------------------------------------------------------------------------
// What the emails say
// ---------------------------------------------------------------------------

const CELL = 'padding:8px 10px;border-bottom:1px solid #e5e5e5;';
const CELL_RIGHT = CELL + 'text-align:right;white-space:nowrap;';

/**
 * The invoice (or reminder) as an email, plain text, and a printable page for the PDF.
 * @param {Row} r
 * @param {{description: string, qty: number, unitPence: number, linePence: number}[]} lines
 * @param {InvoiceState} st
 * @param {string} link
 * @param {Object<string, any>} s
 * @param {boolean} reminder
 */
function invoiceMessage_(r, lines, st, link, s, reminder) {
  const club = String(s['Club name'] || 'The club');
  const no = String(r.get('Invoice no.')).trim();
  const billTo = String(r.get('Bill to')).trim();
  const first = billTo.split(' ')[0] || billTo;
  const intro = String((reminder ? s['Reminder message'] : s['Invoice message']) || '');
  const footer = String(s['Email footer'] || '');
  const subject = reminder
    ? 'Reminder: ' + club + ' invoice ' + no + ' (' + money_(st.balancePence) + ' to pay)'
    : club + ' invoice ' + no + ': ' + money_(st.amountPence);

  const lineRows = lines.map((l) => '<tr><td style="' + CELL + '">' + esc_(l.description) + '</td><td style="' + CELL_RIGHT + '">' +
    l.qty + '</td><td style="' + CELL_RIGHT + '">' + money_(l.unitPence) + '</td><td style="' + CELL_RIGHT + '">' +
    money_(l.linePence) + '</td></tr>').join('');
  const totalRow = (label, pence, bold) => '<tr><td colspan="3" style="' + CELL_RIGHT + (bold ? 'font-weight:bold;' : '') + '">' +
    label + '</td><td style="' + CELL_RIGHT + (bold ? 'font-weight:bold;' : '') + '">' + money_(pence) + '</td></tr>';
  const totals = st.paidPence
    ? totalRow('Total', st.amountPence, false) + totalRow('Paid so far', -st.paidPence, false) + totalRow('To pay', st.balancePence, true)
    : totalRow('Total', st.amountPence, true);
  const table = '<table style="border-collapse:collapse;width:100%;max-width:640px;font-size:15px">' +
    '<tr style="background:#f1f3f4"><th style="' + CELL + 'text-align:left">What for</th><th style="' + CELL_RIGHT + '">Qty</th>' +
    '<th style="' + CELL_RIGHT + '">Price</th><th style="' + CELL_RIGHT + '">Amount</th></tr>' + lineRows + totals + '</table>';
  const facts = '<table style="font-size:15px;margin:12px 0">' +
    '<tr><td style="padding:2px 16px 2px 0;color:#555">Invoice number</td><td><b>' + esc_(no) + '</b></td></tr>' +
    '<tr><td style="padding:2px 16px 2px 0;color:#555">Date</td><td>' + esc_(longDate_(r.get('Date'))) + '</td></tr>' +
    '<tr><td style="padding:2px 16px 2px 0;color:#555">Please pay by</td><td><b>' + esc_(longDate_(r.get('Due'))) + '</b></td></tr></table>';

  const bank = bankDetails_(s, no);
  const pay = link
    ? '<p style="margin:24px 0 8px"><a href="' + esc_(link) + '" style="background:#1a5fb4;color:#ffffff;padding:14px 22px;' +
      'border-radius:6px;text-decoration:none;font-weight:bold;font-size:17px;display:inline-block">Pay ' +
      money_(st.balancePence) + ' by card</a></p><p style="color:#555;font-size:14px;margin-top:0">The button opens a secure ' +
      'payment page run by Stripe.</p>'
    : '';
  const bankHtml = bank ? '<p>' + (link ? 'Or pay' : 'Please pay') + ' by bank transfer to <b>' + esc_(bank.name) + '</b>, sort code <b>' +
    esc_(bank.sortCode) + '</b>, account <b>' + esc_(bank.account) + '</b>, using <b>' + esc_(no) + '</b> as the reference. ' +
    'Bank payments are checked by hand, so they take a few days to show.</p>' : '';
  const otherwise = !link && !bank ? '<p>Please reply to this email to arrange payment.</p>' : '';

  const html = '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#222;max-width:640px">' +
    '<p>Dear ' + esc_(first) + ',</p><p>' + esc_(intro) + '</p>' + facts + table + pay + bankHtml + otherwise +
    '<p>Thank you,<br>' + esc_(club) + '</p>' +
    (footer ? '<p style="color:#777;font-size:13px;margin-top:24px">' + esc_(footer) + '</p>' : '') + '</div>';

  const textLines = lines.map((l) => '  ' + l.description + (l.qty > 1 ? ' x' + l.qty : '') + ': ' + money_(l.linePence));
  const text = 'Dear ' + first + ',\n\n' + intro + '\n\nInvoice number: ' + no + '\nDate: ' + longDate_(r.get('Date')) +
    '\nPlease pay by: ' + longDate_(r.get('Due')) + '\n\n' + textLines.join('\n') + '\n\nTotal: ' + money_(st.amountPence) +
    (st.paidPence ? '\nPaid so far: ' + money_(st.paidPence) + '\nTo pay: ' + money_(st.balancePence) : '') + '\n\n' +
    (link ? 'Pay by card: ' + link + '\n\n' : '') +
    (bank ? 'Bank transfer: ' + bank.name + ', sort code ' + bank.sortCode + ', account ' + bank.account + ', reference ' + no + '\n\n' : '') +
    (!link && !bank ? 'Please reply to this email to arrange payment.\n\n' : '') + 'Thank you,\n' + club + (footer ? '\n\n' + footer : '');

  const printHtml = '<html><head><meta charset="utf-8"></head><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">' +
    '<h1 style="font-size:24px;margin-bottom:0">' + esc_(club) + '</h1><h2 style="font-size:18px;color:#555;margin-top:4px">Invoice</h2>' +
    '<p><b>To:</b> ' + esc_(billTo) + '</p>' + facts + table +
    (link ? '<p>Pay by card: ' + esc_(link) + '</p>' : '') + bankHtml +
    (footer ? '<p style="color:#777;font-size:12px;margin-top:32px">' + esc_(footer) + '</p>' : '') + '</body></html>';

  return { subject, html, text, printHtml };
}

/** Bank details, if Settings says to show them and they are filled in. */
function bankDetails_(s, reference) {
  if (!yes_(s['Show bank details on invoices'])) return null;
  const details = { name: String(s['Bank account name'] || '').trim(), sortCode: String(s['Sort code'] || '').trim(),
    account: String(s['Account number'] || '').trim(), reference };
  return details.name && details.sortCode && details.account ? details : null;
}

/** A PDF copy of the invoice. If Google can't make one, the email still goes, without it. */
function pdfOf_(printHtml, no) {
  try {
    return [Utilities.newBlob(printHtml, MimeType.HTML, no + '.html').getAs(MimeType.PDF).setName('Invoice ' + no + '.pdf')];
  } catch (e) {
    log_('PDF not attached', no + ': ' + errMsg_(e));
    return [];
  }
}

/**
 * Sends one email, or sends it to the test address while test mode is on.
 * @param {string} to
 * @param {string} subject
 * @param {string} html
 * @param {string} text
 * @param {Object<string, any>} s
 * @param {GoogleAppsScript.Base.Blob[]} attachments
 */
function deliver_(to, subject, html, text, s, attachments) {
  let recipient = to;
  let subj = subject;
  let body = html;
  let plain = text;
  if (yes_(s['Test mode'])) {
    recipient = String(s['Test email address'] || '').trim();
    if (!isEmail_(recipient)) throw new Error('Test mode is on, but Settings has no test email address.');
    subj = '[TEST] ' + subject;
    body = '<div style="background:#fff2cc;border:1px solid #f1c232;padding:10px;margin-bottom:16px;font-family:Arial,sans-serif;' +
      'font-size:14px">Test mode: in real use this email would have gone to <b>' + esc_(to) + '</b>.</div>' + html;
    plain = '[Test mode: in real use this would have gone to ' + to + ']\n\n' + text;
  }
  /** @type {Object<string, any>} */
  const message = { to: recipient, subject: subj, htmlBody: body, body: plain, name: String(s['Club name'] || '') };
  const replyTo = String(s['Club email'] || '').trim();
  if (isEmail_(replyTo)) message.replyTo = replyTo;
  if (attachments && attachments.length) message.attachments = attachments;
  MailApp.sendEmail(message);
}

/** Tells the people holding a role about something. Never stops the main job if it fails. */
function notifyRole_(role, subject, text) {
  try {
    const emails = roleEmails_(role);
    if (!emails.length || MailApp.getRemainingDailyQuota() <= EMAIL_SPARE) return;
    const s = settings_();
    const html = '<div style="font-family:Arial,sans-serif;font-size:15px">' + esc_(text).replace(/\n/g, '<br>') + '</div>';
    deliver_(emails.join(','), subject, html, text, s, []);
  } catch (e) {
    log_('Could not email the ' + role, errMsg_(e));
  }
}

function errMsg_(e) {
  const err = /** @type {Error} */ (e);
  return err && err.message ? err.message : String(e);
}
