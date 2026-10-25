/**
 * CLUB REGISTER: forms, applications and course bookings
 * ---------------------------------------------------------------------------
 * Three Google Forms feed the register: join the club, book a course, and
 * ask for a boat park space. Club ▸ Admin ▸ Create the forms makes them and
 * puts their links in Settings, ready for the website.
 *
 * Each answer lands in a grey "Form: …" tab (Google's own copy) and is also
 * tidied onto the Applications, Bookings or Boats tab by onClubFormSubmit().
 */

const FORM_PROPERTY = { join: 'FORM_JOIN_ID', booking: 'FORM_BOOKING_ID', boat: 'FORM_BOAT_ID' };

function createForms() {
  withUi_('Create the forms', () => {
    const props = PropertiesService.getScriptProperties();
    const existing = Object.keys(FORM_PROPERTY).filter((k) => formById_(props.getProperty(FORM_PROPERTY[k])));
    if (existing.length) {
      if (!confirm_('Create the forms', 'The forms already exist.\n\nYes: make brand new forms (the old ones stop sending answers here, ' +
        'and the links on your website will need changing).\nNo: just refresh the choices on the existing forms.')) {
        alert_('Update the forms', updateFormChoices_().join('\n'));
        return;
      }
      existing.forEach((k) => {
        const old = formById_(props.getProperty(FORM_PROPERTY[k]));
        if (old) old.removeDestination();
      });
    }

    const s = settings_();
    const club = String(s['Club name'] || 'Club');
    const privacy = String(s['Privacy notice link'] || '').trim();
    const privacyText = privacy ? '\n\nHow we use your details: ' + privacy : '';

    const join = FormApp.create(club + ': join the club');
    join.setDescription('Fill this in to apply for membership. Our membership secretary will be in touch.' + privacyText);
    join.addTextItem().setTitle('First name').setRequired(true);
    join.addTextItem().setTitle('Last name').setRequired(true);
    join.addTextItem().setTitle('Email address').setRequired(true).setValidation(emailCheck_());
    join.addTextItem().setTitle('Phone');
    join.addParagraphTextItem().setTitle('Address');
    join.addTextItem().setTitle('Postcode');
    join.addListItem().setTitle('Membership').setRequired(true).setChoiceValues(['(choices are added automatically)']);
    join.addParagraphTextItem().setTitle('Tell us about your sailing');
    join.setConfirmationMessage('Thank you. We have your application and will be in touch soon.');

    const booking = FormApp.create(club + ': book a course');
    booking.setDescription('Book a place on a training course. If there is a place, we will email you an invoice to pay by card.' + privacyText);
    booking.addListItem().setTitle('Course').setRequired(true).setChoiceValues(['(choices are added automatically)']);
    booking.addTextItem().setTitle('Your name').setRequired(true);
    booking.addTextItem().setTitle('Email address').setRequired(true).setValidation(emailCheck_());
    booking.addTextItem().setTitle('Phone').setRequired(true);
    booking.addTextItem().setTitle('Participant\'s name (if you are booking for someone else)');
    booking.addMultipleChoiceItem().setTitle('Is the participant under 18?').setRequired(true).setChoiceValues(['No', 'Yes']);
    booking.addParagraphTextItem().setTitle('Medical information or anything else we should know');
    booking.setConfirmationMessage('Thank you. If there is a place, your invoice is on its way by email. If the course is full, you are on the waiting list.');

    const boat = FormApp.create(club + ': boat park space');
    boat.setDescription('Ask for a space in the boat park. Our boat park manager will be in touch.' + privacyText);
    boat.addTextItem().setTitle('Your name').setRequired(true);
    boat.addTextItem().setTitle('Email address').setRequired(true).setValidation(emailCheck_());
    boat.addTextItem().setTitle('Boat name');
    boat.addTextItem().setTitle('Class (e.g. Laser, Wayfarer, kayak)').setRequired(true);
    boat.addTextItem().setTitle('Sail number');
    boat.addListItem().setTitle('What do you need?').setRequired(true).setChoiceValues(['(choices are added automatically)']);
    boat.addParagraphTextItem().setTitle('Anything else');
    boat.setConfirmationMessage('Thank you. Our boat park manager will be in touch.');

    linkForm_(join, 'join', 'Form: join');
    linkForm_(booking, 'booking', 'Form: booking');
    linkForm_(boat, 'boat', 'Form: boat');
    const notes = updateFormChoices_();
    if (!ScriptApp.getProjectTriggers().some((t) => t.getHandlerFunction() === 'onClubFormSubmit')) {
      ScriptApp.newTrigger('onClubFormSubmit').forSpreadsheet(ss_()).onFormSubmit().create();
    }
    setSetting_('Join form', join.getPublishedUrl());
    setSetting_('Booking form', booking.getPublishedUrl());
    setSetting_('Boat form', boat.getPublishedUrl());
    log_('Forms created', 'Join, booking and boat forms. Answers are processed as ' + who_());
    alert_('Create the forms', 'Done. The three forms are in your Google Drive, and their links are on the Settings tab, ready for the website.\n\n' +
      notes.join('\n') + '\n\nForm answers are processed as ' + who_() + '.');
  });
}

function emailCheck_() {
  return FormApp.createTextValidation().setHelpText('Please type an email address.').requireTextIsEmail().build();
}

function formById_(id) {
  if (!id) return null;
  try {
    return FormApp.openById(id);
  } catch (e) {
    return null;
  }
}

/**
 * Sends a form's answers to this spreadsheet and remembers which form is which.
 * @param {GoogleAppsScript.Forms.Form} form
 * @param {string} key
 * @param {string} tabName
 */
function linkForm_(form, key, tabName) {
  const ss = ss_();
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  PropertiesService.getScriptProperties().setProperty(FORM_PROPERTY[key], form.getId());
  SpreadsheetApp.flush();
  const fresh = SpreadsheetApp.openById(ss.getId());
  const sheet = fresh.getSheets().filter((sh) => String(sh.getFormUrl() || '').indexOf(form.getId()) >= 0)[0];
  if (!sheet) return;
  if (!fresh.getSheetByName(tabName)) sheet.setName(tabName);
  sheet.setTabColor('#cccccc');
  fresh.setActiveSheet(sheet);
  fresh.moveActiveSheet(fresh.getNumSheets()); // Google puts new form tabs first; move them to the end
}

/** Menu item: refresh the dropdowns on the forms from the Fees and Courses tabs. */
function updateForms() {
  withUi_('Update the forms', () => {
    const notes = updateFormChoices_();
    alert_('Update the forms', notes.length ? notes.join('\n') : 'There are no forms yet. Use Club ▸ Admin ▸ Create the forms.');
  });
}

function updateFormChoices_() {
  const props = PropertiesService.getScriptProperties();
  const notes = [];
  const fees = feeMap_();
  const priced = (category) => Object.keys(fees).filter((c) => fees[c].category === category && fees[c].pence > 0)
    .map((c) => c + ' (' + money_(fees[c].pence) + ')');

  const join = formById_(props.getProperty(FORM_PROPERTY.join));
  if (join) {
    const choices = priced('Membership');
    setListChoices_(join, 'Membership', choices, 'Membership applications are closed at the moment.');
    notes.push('Join form: ' + choices.length + ' memberships to choose from.');
  }
  const boat = formById_(props.getProperty(FORM_PROPERTY.boat));
  if (boat) {
    const choices = priced('Boat park');
    setListChoices_(boat, 'What do you need?', choices, 'The boat park form is closed at the moment.');
    notes.push('Boat form: ' + choices.length + ' kinds of space.');
  }
  const booking = formById_(props.getProperty(FORM_PROPERTY.booking));
  if (booking) {
    const choices = openCourses_().map((c) => c.id + ': ' + c.name + ', ' + longDate_(c.starts) + ' (' + money_(c.pence) + ')');
    setListChoices_(booking, 'Course', choices, 'There are no courses open for booking at the moment.');
    notes.push('Booking form: ' + (choices.length ? choices.length + ' course(s) open.' : 'no courses open, so the form is closed.'));
  }
  return notes;
}

/**
 * @param {GoogleAppsScript.Forms.Form} form
 * @param {string} title
 * @param {string[]} choices
 * @param {string} closedMessage
 */
function setListChoices_(form, title, choices, closedMessage) {
  const item = form.getItems(FormApp.ItemType.LIST).filter((i) => i.getTitle() === title)[0];
  if (!item) throw new Error('The form "' + form.getTitle() + '" has no "' + title + '" question.');
  if (choices.length) {
    item.asListItem().setChoiceValues(choices);
    form.setAcceptingResponses(true);
  } else {
    form.setCustomClosedFormMessage(closedMessage);
    form.setAcceptingResponses(false);
  }
}

/** Courses that are Open and still have places. */
function openCourses_() {
  const courses = table_(TAB.COURSES);
  fillMissingIds_(courses, 'Course ID', 'C', 3, ['Course']);
  const booked = bookedCounts_();
  return courses.rows
    .filter((c) => String(c.get('Status')).trim() === 'Open' && String(c.get('Course ID')).trim())
    .map((c) => ({
      id: String(c.get('Course ID')).trim(), name: String(c.get('Course')).trim(), starts: c.get('Starts'),
      pence: pence_(c.get('Price £')), left: (Number(c.get('Places')) || 0) - (booked[String(c.get('Course ID')).trim()] || 0),
    }))
    .filter((c) => c.left > 0);
}

/** How many Booked places each course has. */
function bookedCounts_() {
  /** @type {Object<string, number>} */
  const counts = {};
  table_(TAB.BOOKINGS).rows.forEach((b) => {
    if (String(b.get('Status')).trim() === 'Booked') {
      const id = String(b.get('Course ID')).trim();
      counts[id] = (counts[id] || 0) + 1;
    }
  });
  return counts;
}

// ---------------------------------------------------------------------------
// When a form is filled in
// ---------------------------------------------------------------------------

/**
 * Runs (as the person who created the forms) every time a form is submitted.
 * @param {GoogleAppsScript.Events.SheetsOnFormSubmit} e
 */
function onClubFormSubmit(e) {
  try {
    const url = String(e.range.getSheet().getFormUrl() || '');
    const props = PropertiesService.getScriptProperties();
    const which = Object.keys(FORM_PROPERTY).filter((k) => {
      const id = props.getProperty(FORM_PROPERTY[k]);
      return id && url.indexOf(id) >= 0;
    })[0];
    const answers = e.namedValues || {};
    const get = (question) => safe_(answers[question] && answers[question].length ? String(answers[question][0]).trim() : '');
    if (which === 'join') handleJoin_(get);
    if (which === 'booking') handleBooking_(get);
    if (which === 'boat') handleBoat_(get);
  } catch (err) {
    log_('Problem with a form answer', err && /** @type {Error} */ (err).stack ? /** @type {Error} */ (err).stack : String(err));
    notifyRole_(ROLE.ADMIN, 'Problem with a form answer', 'A form answer could not be added to the register: ' + errMsg_(err) +
      '\n\nThe answer is safe in the grey Form tab. Please add it by hand.');
  }
}

function handleJoin_(get) {
  const choice = get('Membership');
  const cut = choice.lastIndexOf(' (£');
  const membership = cut > 0 ? choice.slice(0, cut) : choice;
  const name = (get('First name') + ' ' + get('Last name')).trim();
  appendRows_(table_(TAB.APPLICATIONS), [{
    'Received': today_(), 'First name': get('First name'), 'Last name': get('Last name'), 'Email': get('Email address'),
    'Phone': get('Phone'), 'Address': get('Address'), 'Postcode': get('Postcode'), 'Membership wanted': membership,
    'About them': get('Tell us about your sailing'), 'Decision': 'New',
  }]);
  log_('Join form', name + ' applied for ' + membership);
  notifyRole_(ROLE.MEMBERSHIP, 'New membership application: ' + name, name + ' has applied for ' + membership + ' membership.\n\n' +
    'To accept: open the register, go to the Applications tab, click their row and use Club ▸ Membership ▸ Accept selected application.');
}

function handleBooking_(get) {
  const courseId = get('Course').split(':')[0].trim();
  const course = table_(TAB.COURSES).rows.filter((c) => String(c.get('Course ID')).trim() === courseId)[0];
  const bookings = table_(TAB.BOOKINGS);
  const bookedBy = get('Your name');
  const participant = get('Participant\'s name (if you are booking for someone else)') || bookedBy;
  const email = get('Email address');
  const booked = bookedCounts_()[courseId] || 0;
  const hasPlace = !!course && String(course.get('Status')).trim() === 'Open' && booked < (Number(course.get('Places')) || 0);
  const row = appendRows_(bookings, [{
    'Received': today_(), 'Course ID': courseId, 'Participant': participant, 'Under 18': get('Is the participant under 18?'),
    'Booked by': bookedBy, 'Email': email, 'Phone': get('Phone'), 'Status': hasPlace ? 'Booked' : 'Waiting list',
    'Medical / notes': get('Medical information or anything else we should know'),
  }])[0];
  const s = settings_();
  const courseName = course ? String(course.get('Course')) : 'course ' + courseId;
  let invoiced = '';
  if (course && hasPlace && yes_(s['Invoice course bookings automatically'])) invoiced = invoiceBooking_(row, course, true);
  if (course && !hasPlace) {
    deliver_(email, 'Waiting list: ' + courseName, '<p>Dear ' + esc_(bookedBy.split(' ')[0]) + ',</p><p>Thank you for booking ' +
      esc_(participant) + ' on ' + esc_(courseName) + '. The course is full at the moment, so you are on the waiting list. ' +
      'We will be in touch if a place comes up.</p><p>' + esc_(String(s['Club name'] || '')) + '</p>',
      'Thank you for booking ' + participant + ' on ' + courseName + '. The course is full at the moment, so you are on the waiting list. ' +
      'We will be in touch if a place comes up.', s, []);
  }
  log_('Booking form', participant + ' on ' + courseName + ': ' + (hasPlace ? 'booked' : 'waiting list') + (invoiced ? ', invoice ' + invoiced : ''));
  notifyRole_(ROLE.TRAINING, 'New booking: ' + courseName, participant + ' (booked by ' + bookedBy + ', ' + email + ') ' +
    (hasPlace ? 'has a place on ' : 'is on the waiting list for ') + courseName + '.' + (invoiced ? '\n\nInvoice ' + invoiced + ' has been emailed.' : ''));
  updateFormChoices_();
}

/**
 * Makes (and if asked, emails) the invoice for a course booking. Returns the invoice number.
 * @param {Row} row
 * @param {Row} course
 * @param {boolean} send
 */
function invoiceBooking_(row, course, send) {
  const pence = pence_(course.get('Price £'));
  if (pence <= 0) return '';
  const courseName = String(course.get('Course')).trim();
  const starts = course.get('Starts');
  const email = String(row.get('Email')).trim();
  const description = courseName + (starts instanceof Date ? ' (' + longDate_(starts) + ')' : '') + ': ' + row.get('Participant');
  const no = createInvoices_([{
    memberId: memberIdByEmail_(email), billTo: String(row.get('Booked by')).trim() || String(row.get('Participant')).trim(), email,
    type: 'Training', year: starts instanceof Date ? starts.getFullYear() : '', description,
    lines: [{ description, code: String(course.get('Course ID')), qty: 1, unitPence: pence, ref: String(course.get('Course ID')) }],
  }])[0];
  row.set('Invoice no.', no);
  if (send && isEmail_(email)) sendInvoices_([no]);
  return no;
}

/** Menu item: invoice a booking (e.g. someone moving up from the waiting list). */
function invoiceSelectedBookings() {
  withUi_('Invoice booking', () => {
    const selected = selectedRowNumbers_(TAB.BOOKINGS);
    const bookings = table_(TAB.BOOKINGS);
    const chosen = bookings.rows.filter((b) => selected.indexOf(b.n) >= 0);
    const todo = chosen.filter((b) => !String(b.get('Invoice no.')).trim() && String(b.get('Status')).trim() !== 'Cancelled');
    if (!todo.length) throw new Error('Click on a booking that has no invoice yet (and is not cancelled).');
    const courses = table_(TAB.COURSES);
    const counts = bookedCounts_();
    const problems = [];
    const ready = [];
    todo.forEach((b) => {
      const id = String(b.get('Course ID')).trim();
      const course = courses.rows.filter((c) => String(c.get('Course ID')).trim() === id)[0];
      if (!course) {
        problems.push(b.get('Participant') + ': course ' + id + ' is not on the Courses tab');
        return;
      }
      const isBooked = String(b.get('Status')).trim() === 'Booked';
      if (!isBooked && (counts[id] || 0) >= (Number(course.get('Places')) || 0)) {
        problems.push(b.get('Participant') + ': ' + course.get('Course') + ' is full. Add a place on the Courses tab first.');
        return;
      }
      if (!isBooked) counts[id] = (counts[id] || 0) + 1;
      ready.push({ b, course });
    });
    if (!ready.length) throw new Error(problems.join('\n'));
    if (!confirm_('Invoice booking', 'Give ' + ready.map((x) => x.b.get('Participant')).join(', ') + ' a place and email the invoice(s)?' +
      (problems.length ? '\n\nSkipped:\n' + listText_(problems) : ''))) return;
    const numbers = ready.map((x) => {
      x.b.set('Status', 'Booked');
      return invoiceBooking_(x.b, x.course, true);
    }).filter(String);
    updateFormChoices_();
    log_('Bookings invoiced', numbers.join(', '));
    alert_('Invoice booking', numbers.length ? 'Done: ' + numbers.join(', ') + '. Check the Invoices tab to see they were emailed.' : 'Done (no charge for these courses).');
  });
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

/** Menu item: accept the selected application(s): new member, first invoice, email. */
function acceptSelectedApplications() {
  withUi_('Accept application', () => {
    const selected = selectedRowNumbers_(TAB.APPLICATIONS);
    const applications = table_(TAB.APPLICATIONS);
    const chosen = applications.rows.filter((r) => selected.indexOf(r.n) >= 0 &&
      ['', 'New'].indexOf(String(r.get('Decision')).trim()) >= 0);
    if (!chosen.length) throw new Error('Click on an application whose Decision is New.');
    const s = settings_();
    const year = Number(s['Membership year']) || new Date().getFullYear();
    const fees = feeMap_();
    chosen.forEach((r) => {
      const code = String(r.get('Membership wanted')).trim();
      if (!fees[code] || fees[code].category !== 'Membership') {
        throw new Error(fullName_(r) + ' asked for "' + code + '", which is not a membership on the Fees tab. ' +
          'Change their Membership wanted to one that is, then try again.');
      }
    });
    const already = chosen.filter((r) => memberIdByEmail_(r.get('Email')))
      .map((r) => fullName_(r) + ': ' + r.get('Email') + ' already belongs to member ' + memberIdByEmail_(r.get('Email')));
    if (!confirm_('Accept application', 'Accept ' + chosen.map(fullName_).join(', ') + '?\n\nEach gets a Member ID and a ' + year +
      ' membership invoice by email. They become Active when they pay.' +
      (already.length ? '\n\nCheck first (they may already be members):\n' + listText_(already) : ''))) return;

    const members = table_(TAB.MEMBERS);
    fillMissingIds_(members, 'Member ID', 'M', 4, ['First name', 'Last name']);
    const ids = nextIds_(members, 'Member ID', 'M', 4, chosen.length);
    appendRows_(members, chosen.map((r, i) => {
      const free = fees[String(r.get('Membership wanted')).trim()].pence <= 0;
      return {
        'Member ID': ids[i], 'First name': r.get('First name'), 'Last name': r.get('Last name'), 'Email': r.get('Email'),
        'Phone': r.get('Phone'), 'Address': r.get('Address'), 'Postcode': r.get('Postcode'),
        'Membership': String(r.get('Membership wanted')).trim(), 'Status': free ? 'Active' : 'Applicant', 'Joined': free ? today_() : '',
        'Notes': 'Application received ' + longDate_(r.get('Received')),
      };
    }));
    /** @type {Draft[]} */
    const drafts = [];
    chosen.forEach((r, i) => {
      const code = String(r.get('Membership wanted')).trim();
      const fee = fees[code];
      if (fee.pence <= 0) return;
      const description = fee.description + ' ' + year;
      drafts.push({
        memberId: ids[i], billTo: fullName_(r), email: String(r.get('Email')).trim(), type: 'Membership', year, description,
        lines: [{ description, code, qty: 1, unitPence: fee.pence, ref: ids[i] }],
      });
    });
    const numbers = createInvoices_(drafts);
    chosen.forEach((r, i) => {
      r.set('Decision', 'Accepted');
      r.set('Member ID', ids[i]);
    });
    const sent = numbers.length ? sendInvoices_(numbers) : null;
    log_('Applications accepted', chosen.map((r, i) => fullName_(r) + ' (' + ids[i] + ')').join(', ') +
      (numbers.length ? '; invoices ' + numbers.join(', ') : ''));
    alert_('Accept application', 'Accepted: ' + chosen.map((r, i) => fullName_(r) + ' (' + ids[i] + ')').join(', ') + '.' +
      (sent ? '\n\n' + sendSummary_(sent) : ''));
  });
}

function handleBoat_(get) {
  const email = get('Email address');
  const ownerId = memberIdByEmail_(email);
  const members = memberMap_();
  const boats = table_(TAB.BOATS);
  const id = nextIds_(boats, 'Boat ID', 'B', 4, 1)[0];
  const choice = get('What do you need?');
  const cut = choice.lastIndexOf(' (£');
  const fee = cut > 0 ? choice.slice(0, cut) : choice;
  appendRows_(boats, [{
    'Boat ID': id, 'Owner': ownerId ? members[ownerId].name + ' (' + ownerId + ')' : '', 'Boat name': get('Boat name'),
    'Class': get('Class (e.g. Laser, Wayfarer, kayak)'), 'Sail number': get('Sail number'), 'Boat park fee': fee, 'Status': 'Waiting for space',
    'Notes': (ownerId ? '' : 'Asked for by ' + get('Your name') + ' (' + email + '), who is not matched to a member. ') + get('Anything else'),
  }]);
  log_('Boat form', get('Your name') + ' asked for ' + fee + (ownerId ? ' (' + ownerId + ')' : ' (not matched to a member)'));
  notifyRole_(ROLE.BOATPARK, 'Boat park request from ' + get('Your name'), get('Your name') + ' (' + email + ') has asked for ' + fee +
    ' for ' + (get('Boat name') || 'a boat') + '.\n\nIt is on the Boats tab as ' + id + ', Waiting for space.');
}
