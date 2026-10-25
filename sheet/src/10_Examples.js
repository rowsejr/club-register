/**
 * CLUB REGISTER: example data for trying things out
 * ---------------------------------------------------------------------------
 * Club ▸ Admin ▸ Add example data fills an empty register with made-up people
 * and boats, so the team can practise. The prices are the club's 2026 price
 * list, as a starting point. Use a separate copy of the spreadsheet for this,
 * not the real register.
 */

function addExampleData() {
  withUi_('Add example data', () => {
    if (table_(TAB.MEMBERS).rows.length || table_(TAB.BOATS).rows.length || table_(TAB.INVOICES).rows.length) {
      throw new Error('Example data can only go into an empty register, and this one already has members, boats or invoices.');
    }
    const s = settings_();
    if (!confirm_('Add example data', 'Add made-up members, boats, courses and calendar dates, plus a price list, so you can try ' +
      'things out?\n\nTest mode stays on, so emails only go to ' + s['Test email address'] + '.')) return;
    seedExamples_();
    setSetting_('Test mode', 'Yes');
    log_('Example data added', 'Made-up members, boats, courses, applications and calendar dates');
    ss_().getSheetByName(TAB.MEMBERS).activate();
    alert_('Add example data', 'Done. Things to try:\n\n' +
      '• Club ▸ Membership ▸ Prepare membership invoices, then Club ▸ Invoices ▸ Send draft invoices\n' +
      '• Club ▸ Boat park ▸ Prepare boat park invoices\n' +
      '• Club ▸ Invoices ▸ New invoice\n' +
      '• On Applications, click a row, then Club ▸ Membership ▸ Accept selected application\n' +
      '• Add a bank payment on the Payments tab and watch the invoice turn green');
  });
}

function seedExamples_() {
  const day = (text) => Utilities.parseDate(text, tz_(), 'yyyy-MM-dd');
  const s = settings_();
  const year = Number(s['Membership year']) || new Date().getFullYear() + 1;

  appendRows_(table_(TAB.FEES), [
    ['Single Adult Sailing', 'Membership', 'Single adult sailing membership', 108],
    ['Sailing Adult + Partner', 'Membership', 'Sailing adult and partner membership', 153],
    ['Sailing Family 1A+1C', 'Membership', 'Sailing family membership (1 adult, 1 child)', 114],
    ['Sailing Family 1A+2C', 'Membership', 'Sailing family membership (1 adult, 2 children)', 120],
    ['Sailing Family 2A+1C', 'Membership', 'Sailing family membership (2 adults, 1 child)', 159],
    ['Sailing Family 2A+2C', 'Membership', 'Sailing family membership (2 adults, 2 children)', 165],
    ['Sailing Family 2A+3C', 'Membership', 'Sailing family membership (2 adults, 3 children)', 171],
    ['Sailing Student Member', 'Membership', 'Sailing student membership', 76.5],
    ['Sailing Adult (6 months)', 'Membership', 'Sailing adult membership (6 months)', 54],
    ['Temporary Sailing Adult (3 months)', 'Membership', 'Temporary sailing adult membership (3 months)', 30],
    ['Non-Sailing Adult', 'Membership', 'Non-sailing adult membership', 60],
    ['Non Sailing + 1C', 'Membership', 'Non-sailing membership with 1 child', 64.5],
    ['Non Sailing + 2C', 'Membership', 'Non-sailing membership with 2 children', 70.5],
    ['Non Sailing + 3C', 'Membership', 'Non-sailing membership with 3 children', 76.5],
    ['Honorary Member', 'Membership', 'Honorary membership', 0],
    ['RYA Course Membership', 'Membership', 'RYA course membership', 0],
    ['Boat park space', 'Boat park', 'Boat park space', 95],
    ['Kayak / SUP rack', 'Boat park', 'Kayak or paddle board rack space', 30],
    ['Gate key deposit', 'Other', 'Gate key deposit (refundable)', 15],
    ['Laying-up supper ticket', 'Other', 'Laying-up supper ticket', 18],
  ].map((f) => ({ 'Code': f[0], 'Category': f[1], 'Description on invoice': f[2], 'Amount £': f[3], 'Notes': 'Example: 2026 price' })));

  const people = [
    ['Alice', 'Hughes', 'Single Adult Sailing', 'Active', ''],
    ['Ben', 'Carter', 'Sailing Family 2A+2C', 'Active', 'Sophie, Tom and Lily Carter'],
    ['Chloe', 'Patel', 'Sailing Adult + Partner', 'Active', 'Raj Patel'],
    ['David', 'Okafor', 'Non-Sailing Adult', 'Active', ''],
    ['Emma', 'Lewis', 'Single Adult Sailing', 'Active', ''],
    ['Frank', 'Morris', 'Single Adult Sailing', 'Active', ''],
    ['Grace', 'Kim', 'Sailing Family 1A+2C', 'Active', 'Ella and Noah Kim'],
    ['Harry', 'Walsh', 'Sailing Student Member', 'Active', ''],
    ['Isla', 'Reid', 'Single Adult Sailing', 'Active', ''],
    ['Jack', 'Turner', 'Sailing Family 2A+1C', 'Active', 'Megan and Oscar Turner'],
    ['Kate', 'Bennett', 'Non Sailing + 1C', 'Active', 'Ruby Bennett'],
    ['Liam', 'Foster', 'Single Adult Sailing', 'Active', ''],
    ['Maya', 'Shah', 'Sailing Adult + Partner', 'Active', 'Dev Shah'],
    ['Nick', 'Rowe', 'Single Adult Sailing', 'Active', ''],
    ['Olivia', 'Grant', 'Honorary Member', 'Active', ''],
    ['Paul', 'Murphy', 'Single Adult Sailing', 'Active', ''],
    ['Rosa', 'Silva', 'Sailing Family 1A+1C', 'Active', 'Leo Silva'],
    ['Sam', 'Doyle', 'Single Adult Sailing', 'Lapsed', ''],
    ['Tara', 'Quinn', 'Non-Sailing Adult', 'Left', ''],
    ['Umar', 'Ali', 'Single Adult Sailing', 'Active', ''],
    ['Vicky', 'Price', 'Sailing Adult (6 months)', 'Active', ''],
    ['Will', 'Hart', 'Single Adult Sailing', 'Applicant', ''],
  ];
  const streets = ['Harbour Street', 'Quay Lane', 'Marine Parade', 'Church Street', 'Sea View Road', 'Mill Lane'];
  appendRows_(table_(TAB.MEMBERS), people.map((p, i) => ({
    'Member ID': 'M' + String(i + 1).padStart(4, '0'),
    'First name': p[0],
    'Last name': p[1],
    'Email': p[1] === 'Murphy' ? '' : (p[0] + '.' + p[1] + '@example.com').toLowerCase(),
    'Phone': '07700 900' + String(100 + i * 37).slice(-3),
    'Address': (i * 7 % 90 + 1) + ' ' + streets[i % streets.length] + ', Seatown',
    'Postcode': 'ZZ' + (i % 9 + 1) + ' ' + (i % 7 + 1) + 'AB',
    'Membership': p[2],
    'Status': p[3],
    'Joined': p[3] === 'Applicant' ? '' : day((2015 + (i % 10)) + '-0' + (i % 9 + 1) + '-15'),
    'Family members': p[4],
    'Notes': p[1] === 'Murphy' ? 'Example: no email address, so his invoices cannot be emailed' : '',
  })));

  const owner = (i) => people[i][0] + ' ' + people[i][1] + ' (M' + String(i + 1).padStart(4, '0') + ')';
  appendRows_(table_(TAB.BOATS), [
    [owner(1), 'Wild Goose', 'Wayfarer', '10234', 'C7', 'Boat park space', 'In park', year + '-03-31'],
    [owner(1), 'Little Tern', 'Mirror', '70512', 'C8', 'Boat park space', 'In park', year + '-03-31'],
    [owner(0), 'Blue Streak', 'Laser', '211456', 'B9', 'Boat park space', 'In park', '2026-08-01'],
    [owner(4), 'Kestrel', 'RS200', '1234', 'D4', 'Boat park space', 'In park', year + '-05-01'],
    [owner(9), 'Puffin', 'Enterprise', '23001', 'D9', 'Boat park space', 'In park', year + '-04-12'],
    [owner(11), 'Shearwater', 'Contender', '1876', 'C12', 'Boat park space', 'In park', year + '-02-28'],
    [owner(13), 'Gull', 'Streaker', '1650', 'C14', 'Boat park space', 'In park', ''],
    [owner(12), 'Red Kayak', 'Kayak', '', 'R1', 'Kayak / SUP rack', 'In park', ''],
    [owner(19), 'Seal', 'Osprey', '1302', '', 'Boat park space', 'Waiting for space', ''],
    [owner(17), 'Old Faithful', 'Wayfarer', '9001', 'D13', 'Boat park space', 'Left', ''],
    [owner(15), 'Curlew', 'Miracle', '3345', 'C16', 'Boat park space', 'In park', ''],
    ['', 'Mystery Topper', 'Topper', '', 'B2', 'Boat park space', 'In park', ''],
  ].map((b, i) => ({
    'Boat ID': 'B' + String(i + 1).padStart(4, '0'), 'Owner': b[0], 'Boat name': b[1], 'Class': b[2], 'Sail number': b[3], 'Space': b[4],
    'Boat park fee': b[5], 'Status': b[6], 'Insurance expiry': b[7] ? day(String(b[7])) : '',
    'Notes': b[0] ? '' : 'Example: nobody knows whose this is, so the boat park run will flag it',
  })));

  appendRows_(table_(TAB.COURSES), [
    ['C001', 'RYA Start Sailing (Level 1)', year + '-05-15', year + '-05-16', 6, 150],
    ['C002', 'RYA Basic Skills (Level 2)', year + '-06-12', year + '-06-13', 6, 150],
    ['C003', 'RYA Powerboat Level 2', year + '-07-03', year + '-07-04', 2, 180],
  ].map((c) => ({
    'Course ID': c[0], 'Course': c[1], 'Starts': day(String(c[2])), 'Ends': day(String(c[3])), 'Places': c[4], 'Price £': c[5], 'Status': 'Open',
  })));

  appendRows_(table_(TAB.BOOKINGS), [
    ['C003', 'Ella Kim', 'Yes', 'Grace Kim', 'grace.kim@example.com', 'Booked'],
    ['C003', 'Harry Walsh', 'No', 'Harry Walsh', 'harry.walsh@example.com', 'Booked'],
    ['C003', 'Liam Foster', 'No', 'Liam Foster', 'liam.foster@example.com', 'Waiting list'],
  ].map((b) => ({
    'Received': today_(), 'Course ID': b[0], 'Participant': b[1], 'Under 18': b[2], 'Booked by': b[3], 'Email': b[4], 'Status': b[5],
    'Medical / notes': 'Example booking',
  })));

  appendRows_(table_(TAB.APPLICATIONS), [
    ['Zoe', 'Martin', 'zoe.martin@example.com', 'Single Adult Sailing', 'Sailed dinghies at university, keen to race.'],
    ['Yusuf', 'Khan', 'yusuf.khan@example.com', 'Sailing Family 2A+1C', 'Family new to sailing; our son did a taster day.'],
  ].map((a) => ({
    'Received': today_(), 'First name': a[0], 'Last name': a[1], 'Email': a[2], 'Phone': '07700 900999', 'Address': '1 Example Road, Seatown',
    'Postcode': 'ZZ1 1AA', 'Membership wanted': a[3], 'About them': a[4], 'Decision': 'New',
  })));

  appendRows_(table_(TAB.CALENDAR), [
    [year + '-03-06', '10:00', '15:00', 'Fitting-out work party', 'Clubhouse', 'Bring gloves. Lunch provided.', 'Work party'],
    [year + '-04-03', '', '', 'Season opens', 'Clubhouse', '', 'Social'],
    [year + '-04-11', '11:00', '15:00', 'Spring series, race 1', 'Club line', 'Briefing 10:15.', 'Racing'],
    [year + '-05-15', '09:30', '17:00', 'RYA Start Sailing (day 1)', 'Slipway', '', 'Training'],
    [year + '-06-19', '', '', 'Club regatta', 'Club line', '', 'Racing'],
    [year + '-09-18', '19:00', '23:00', 'Laying-up supper', 'Clubhouse', 'Tickets from the treasurer.', 'Social'],
    [year + '-11-13', '19:30', '21:30', 'Annual general meeting', 'Clubhouse', '', 'Meeting'],
  ].map((e) => ({
    'Date': day(String(e[0])), 'Start': e[1], 'End': e[2], 'Event': e[3], 'Where': e[4], 'Details': e[5], 'Type': e[6], 'Status': 'Confirmed',
  })));
}
