/**
 * CLUB REGISTER: the "New invoice" sidebar
 * ---------------------------------------------------------------------------
 * The page that opens on the right of the spreadsheet for one-off invoices.
 * It asks the spreadsheet for members and fees (builderData) and sends the
 * finished invoice back (builderCreate), both in 04_Invoices.js.
 */

function builderHtml_() {
  return `<!DOCTYPE html>
<html>
<head>
<base target="_top">
<meta charset="utf-8">
<style>
  body { font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #222; margin: 0; padding: 12px; }
  label { display: block; font-weight: bold; margin: 12px 0 4px; }
  input, select { width: 100%; box-sizing: border-box; font-size: 14px; padding: 7px; border: 1px solid #bbb; border-radius: 4px; background: #fff; }
  .two { display: flex; gap: 8px; }
  .two > div { flex: 1; }
  .line { border: 1px solid #ddd; border-radius: 6px; padding: 8px; margin: 8px 0; background: #fafafa; }
  .line input, .line select { margin-top: 6px; }
  .line .sums { display: flex; gap: 6px; align-items: center; margin-top: 6px; }
  .line .sums input { margin-top: 0; }
  .line .qty { width: 60px; flex: none; }
  .line .amount { width: 80px; flex: none; text-align: right; font-weight: bold; }
  .remove { background: none; border: none; color: #a00; font-size: 13px; cursor: pointer; padding: 0; margin-top: 6px; }
  .add { background: #fff; border: 1px dashed #1a5fb4; color: #1a5fb4; width: 100%; padding: 8px; border-radius: 6px; cursor: pointer; font-size: 14px; }
  .total { font-size: 18px; font-weight: bold; margin: 14px 0 4px; text-align: right; }
  .main { width: 100%; padding: 11px; font-size: 15px; margin-top: 10px; border-radius: 6px; border: 1px solid #1a5fb4; cursor: pointer; }
  .primary { background: #1a5fb4; color: #fff; }
  .secondary { background: #fff; color: #1a5fb4; }
  .main:disabled { opacity: 0.5; cursor: default; }
  .banner { background: #fff2cc; border: 1px solid #f1c232; padding: 8px; border-radius: 6px; font-size: 13px; margin-bottom: 4px; }
  .msg { margin-top: 12px; padding: 10px; border-radius: 6px; white-space: pre-wrap; }
  .ok { background: #d9ead3; }
  .err { background: #f4cccc; }
  .hint { color: #555; font-size: 12px; margin-top: 4px; }
  .choice { display: flex; gap: 16px; margin: 4px 0 6px; }
  .choice label { font-weight: normal; margin: 0; display: flex; align-items: center; gap: 4px; }
  .choice input { width: auto; }
</style>
</head>
<body>
<div id="loading">Loading…</div>
<div id="form" style="display:none">
  <div id="banner" class="banner" style="display:none"></div>

  <label>Who is it for?</label>
  <div class="choice">
    <label><input type="radio" name="who" value="member" checked> A member</label>
    <label><input type="radio" name="who" value="other"> Someone else</label>
  </div>
  <div id="memberBox">
    <input id="member" list="members" placeholder="Start typing a name" autocomplete="off">
    <datalist id="members"></datalist>
    <div class="hint" id="memberHint"></div>
  </div>
  <div id="otherBox" style="display:none">
    <input id="billTo" placeholder="Name">
    <input id="email" placeholder="Email address" style="margin-top:6px">
  </div>

  <div class="two">
    <div><label for="type">Kind</label><select id="type"></select></div>
    <div><label for="year">Year</label><input id="year" type="number" min="2000" max="2100"></div>
  </div>

  <label>What for</label>
  <div id="lines"></div>
  <button class="add" id="addLine" type="button">+ Add a line</button>
  <div class="total">Total <span id="total">£0.00</span></div>

  <label for="due">Pay within (days)</label>
  <input id="due" type="number" min="0" max="365">

  <button class="main primary" id="sendButton" type="button">Save and email it now</button>
  <button class="main secondary" id="draftButton" type="button">Save as a draft</button>
  <div id="message" class="msg" style="display:none"></div>
</div>

<script>
var DATA = null;
var membersByLabel = {};
var feesByCode = {};

function el(id) { return document.getElementById(id); }

function money(pence) {
  var sign = pence < 0 ? '-' : '';
  var parts = (Math.abs(Math.round(pence)) / 100).toFixed(2).split('.');
  return sign + '£' + parts[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',') + '.' + parts[1];
}

function who() {
  var picked = document.querySelector('input[name="who"]:checked');
  return picked ? picked.value : 'member';
}

function start(d) {
  DATA = d;
  d.members.forEach(function (m) {
    var label = m.name + ' (' + m.id + ')';
    membersByLabel[label] = m;
    var option = document.createElement('option');
    option.value = label;
    el('members').appendChild(option);
  });
  d.fees.forEach(function (f) { feesByCode[f.code] = f; });
  d.types.forEach(function (t) {
    var option = document.createElement('option');
    option.value = t;
    option.textContent = t;
    el('type').appendChild(option);
  });
  el('type').value = 'Other';
  el('year').value = d.year;
  el('due').value = d.dueDays;
  var notes = [];
  if (d.testMode) notes.push('Test mode is on: emails go to ' + d.testEmail + ', not to members.');
  if (!d.stripe) notes.push('Stripe is not connected, so emails will not have a pay-by-card button.');
  if (notes.length) { el('banner').textContent = notes.join(' '); el('banner').style.display = 'block'; }
  addLine();
  el('loading').style.display = 'none';
  el('form').style.display = 'block';
}

function addLine() {
  var box = document.createElement('div');
  box.className = 'line';

  var fee = document.createElement('select');
  fee.className = 'fee';
  var own = document.createElement('option');
  own.value = '';
  own.textContent = 'Type my own';
  fee.appendChild(own);
  ['Membership', 'Boat park', 'Other'].forEach(function (category) {
    var group = document.createElement('optgroup');
    group.label = category;
    DATA.fees.filter(function (f) { return f.category === category; }).forEach(function (f) {
      var option = document.createElement('option');
      option.value = f.code;
      option.textContent = f.code + ' (' + money(f.pence) + ')';
      group.appendChild(option);
    });
    if (group.children.length) fee.appendChild(group);
  });

  var description = document.createElement('input');
  description.className = 'description';
  description.placeholder = 'Description';

  var sums = document.createElement('div');
  sums.className = 'sums';
  var qty = document.createElement('input');
  qty.className = 'qty';
  qty.type = 'number';
  qty.min = '1';
  qty.step = '1';
  qty.value = '1';
  qty.title = 'How many';
  var unit = document.createElement('input');
  unit.className = 'unit';
  unit.type = 'number';
  unit.step = '0.01';
  unit.placeholder = 'Price £';
  var amount = document.createElement('span');
  amount.className = 'amount';
  amount.textContent = money(0);
  sums.appendChild(qty);
  sums.appendChild(document.createTextNode('×'));
  sums.appendChild(unit);
  sums.appendChild(amount);

  var remove = document.createElement('button');
  remove.className = 'remove';
  remove.type = 'button';
  remove.textContent = 'Remove this line';
  remove.onclick = function () {
    box.parentNode.removeChild(box);
    if (!document.querySelectorAll('.line').length) addLine();
    recalc();
  };

  fee.onchange = function () {
    var f = feesByCode[fee.value];
    if (f) {
      var withYear = f.category === 'Membership' || f.category === 'Boat park';
      description.value = f.description + (withYear && el('year').value ? ' ' + el('year').value : '');
      unit.value = (f.pence / 100).toFixed(2);
      if (f.category === 'Membership' || f.category === 'Boat park') el('type').value = f.category;
    }
    recalc();
  };
  qty.oninput = recalc;
  unit.oninput = recalc;

  box.appendChild(fee);
  box.appendChild(description);
  box.appendChild(sums);
  box.appendChild(remove);
  el('lines').appendChild(box);
}

function readLines() {
  var out = [];
  var boxes = document.querySelectorAll('.line');
  for (var i = 0; i < boxes.length; i++) {
    var b = boxes[i];
    out.push({
      code: b.querySelector('.fee').value,
      description: b.querySelector('.description').value,
      qty: b.querySelector('.qty').value,
      unit: b.querySelector('.unit').value
    });
  }
  return out;
}

function recalc() {
  var total = 0;
  var boxes = document.querySelectorAll('.line');
  for (var i = 0; i < boxes.length; i++) {
    var qty = Number(boxes[i].querySelector('.qty').value) || 0;
    var pence = Math.round((Number(boxes[i].querySelector('.unit').value) || 0) * 100);
    var line = Math.round(qty * pence);
    boxes[i].querySelector('.amount').textContent = money(line);
    total += line;
  }
  el('total').textContent = money(total);
}

function showMember() {
  var m = membersByLabel[el('member').value];
  el('memberHint').textContent = !m ? '' : (m.email ? 'It will be emailed to ' + m.email + '.' : 'There is no email address for this member, so it can only be saved as a draft.');
}

function busy(on) {
  el('sendButton').disabled = on;
  el('draftButton').disabled = on;
}

function say(text, ok) {
  el('message').textContent = text;
  el('message').className = 'msg ' + (ok ? 'ok' : 'err');
  el('message').style.display = 'block';
}

function submit(send) {
  var payload = { type: el('type').value, year: el('year').value, dueDays: el('due').value, lines: readLines(), send: send };
  if (who() === 'member') {
    var m = membersByLabel[el('member').value];
    if (!m) { say('Pick a member from the list: start typing their name and choose it.', false); return; }
    payload.memberId = m.id;
  } else {
    payload.billTo = el('billTo').value;
    payload.email = el('email').value;
  }
  busy(true);
  say('Working…', true);
  google.script.run.withSuccessHandler(done).withFailureHandler(failed).builderCreate(payload);
}

function done(result) {
  busy(false);
  say('Invoice ' + result.invoiceNo + ' (' + result.total + '): ' + result.message, true);
  el('lines').innerHTML = '';
  addLine();
  recalc();
}

function failed(error) {
  var text = (error && error.message) ? error.message : String(error);
  if (!DATA) { el('loading').textContent = 'Could not load: ' + text; return; }
  busy(false);
  say(text, false);
}

document.querySelectorAll('input[name="who"]').forEach(function (radio) {
  radio.onchange = function () {
    el('memberBox').style.display = who() === 'member' ? 'block' : 'none';
    el('otherBox').style.display = who() === 'member' ? 'none' : 'block';
  };
});
el('member').oninput = showMember;
el('member').onchange = showMember;
el('addLine').onclick = function () { addLine(); };
el('sendButton').onclick = function () { submit(true); };
el('draftButton').onclick = function () { submit(false); };

google.script.run.withSuccessHandler(start).withFailureHandler(failed).builderData();
</script>
</body>
</html>`;
}
