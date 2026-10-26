/**
 * A read-only copy of the club office, in one of two spreadsheets:
 *
 * - the **committee** copy: members (no email, mobile or money), boats, coming events, courses, bookings and the
 *   RYA census, shared with the whole committee;
 * - the **treasurer** copy: who owes money, payments, the money summary and members with what they owe, shared only
 *   with the Treasurer. (Google cannot hide one tab from people who can open a spreadsheet, so money has its own file.)
 *
 * Each refreshes itself every hour.
 *
 * Nobody types into it. The tabs are protected, and anything changed here is overwritten at the next refresh.
 * To change anything, use the club office.
 *
 * How it asks the office: each request is signed with the "sheet" key the office administrator set up
 * (CLUB_FEED_KEYS on the office). The office refuses old, repeated or wrongly signed requests.
 */

var COPIES = {
  committee: [
    ['siteMembers', 'Members'],
    ['boats', 'Boats'],
    ['events', 'Coming events'],
    ['courses', 'Courses'],
    ['bookings', 'Bookings'],
    ['census', 'RYA census']
  ],
  treasurer: [
    ['chase', 'Who owes'],
    ['payments', 'Payments'],
    ['money', 'Money summary'],
    ['members', 'Members and money']
  ]
};

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Club office')
    .addItem('Refresh now', 'refreshAll')
    .addSeparator()
    .addItem('Set up (administrator only)', 'setUp')
    .addToUi();
}

/** Asks for the office address and the key once, then refreshes every hour. */
function setUp() {
  var ui = SpreadsheetApp.getUi();
  var address = ui.prompt('Club office address', 'For example https://office.margateyachtclub.co.uk', ui.ButtonSet.OK_CANCEL);
  if (address.getSelectedButton() !== ui.Button.OK) return;
  var key = ui.prompt('Sheet key', 'The "sheet" key the office administrator gave you.', ui.ButtonSet.OK_CANCEL);
  if (key.getSelectedButton() !== ui.Button.OK) return;
  var copy = ui.prompt('Which copy is this?', 'Type committee (shared with the committee, no money) or treasurer (only for the Treasurer).', ui.ButtonSet.OK_CANCEL);
  if (copy.getSelectedButton() !== ui.Button.OK) return;
  var which = copy.getResponseText().trim().toLowerCase();
  if (!COPIES[which]) { ui.alert('Please type committee or treasurer.'); return; }
  PropertiesService.getScriptProperties().setProperties({
    OFFICE_URL: address.getResponseText().trim().replace(/\/+$/, ''),
    FEED_KEY: key.getResponseText().trim(),
    COPY: which
  });
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'refreshAll') ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger('refreshAll').timeBased().everyHours(1).create();
  refreshAll();
  ui.alert('Done. The tabs now refresh every hour.');
}

function toHex(bytes) {
  return bytes.map(function (b) {
    var v = (b < 0 ? b + 256 : b).toString(16);
    return v.length === 1 ? '0' + v : v;
  }).join('');
}

/** The same signature the office checks (app/feeds.py). */
function signature(secret, name, client, ts, nonce) {
  return toHex(Utilities.computeHmacSha256Signature(name + '\n' + client + '\n' + ts + '\n' + nonce, secret));
}

function feedUrl(base, secret, name, nowMillis, nonce) {
  var ts = String(Math.floor(nowMillis / 1000));
  return base + '/feeds/' + name + '.json?client=sheet&ts=' + ts + '&nonce=' + nonce + '&sig=' + signature(secret, name, 'sheet', ts, nonce);
}

function fetchFeed(name) {
  var props = PropertiesService.getScriptProperties();
  var base = props.getProperty('OFFICE_URL');
  var secret = props.getProperty('FEED_KEY');
  if (!base || !secret) throw new Error('Not set up yet: choose Club office > Set up.');
  var nonce = Utilities.getUuid().replace(/-/g, '');
  var response = UrlFetchApp.fetch(feedUrl(base, secret, name, Date.now(), nonce), { muteHttpExceptions: true });
  if (response.getResponseCode() !== 200) {
    throw new Error(name + ': the office answered ' + response.getResponseCode() + ' ' + response.getContentText().slice(0, 200));
  }
  return JSON.parse(response.getContentText());
}

/** Replaces one tab with a feed: a bold header row, the rows, and a line saying where it came from. */
function writeTab(spreadsheet, title, feed) {
  var sheet = spreadsheet.getSheetByName(title) || spreadsheet.insertSheet(title);
  sheet.clearContents();
  var rows = [feed.columns].concat(feed.rows);
  sheet.getRange(1, 1, rows.length, feed.columns.length).setValues(rows);
  sheet.getRange(1, 1, 1, feed.columns.length).setFontWeight('bold');
  sheet.setFrozenRows(1);
  var when = String(feed.generated_at).replace('T', ' ').slice(0, 16);
  sheet.getRange(rows.length + 2, 1).setValue('Copied from the club office at ' + when + ' (UTC). Do not edit here: change things in the club office.');
  protect(sheet);
}

/** Only the owner of the spreadsheet (whose account runs the refresh) can change a tab. */
function protect(sheet) {
  var existing = sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET);
  var protection = existing.length ? existing[0] : sheet.protect();
  protection.setDescription('Copied from the club office: change things there, not here');
  var me = Session.getEffectiveUser();
  protection.addEditor(me);
  var others = protection.getEditors().filter(function (user) { return user.getEmail() !== me.getEmail(); });
  if (others.length) protection.removeEditors(others);
  if (protection.canDomainEdit()) protection.setDomainEdit(false);
}

/** The tabs this spreadsheet holds: the committee copy unless set up as the Treasurer's. */
function feedsFor(copy) {
  return COPIES[copy] || COPIES.committee;
}

function refreshAll() {
  var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  var problems = [];
  feedsFor(PropertiesService.getScriptProperties().getProperty('COPY')).forEach(function (pair) {
    try {
      writeTab(spreadsheet, pair[1], fetchFeed(pair[0]));
    } catch (e) {
      problems.push(String(e && e.message ? e.message : e));
    }
  });
  if (problems.length) throw new Error(problems.join('\n')); // Google emails the owner when an hourly refresh fails
}

if (typeof module !== 'undefined') {
  module.exports = { COPIES: COPIES, feedsFor: feedsFor, toHex: toHex, signature: signature, feedUrl: feedUrl, writeTab: writeTab, refreshAll: refreshAll };
}
