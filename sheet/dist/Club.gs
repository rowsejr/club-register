/**
 * CLUB REGISTER
 * Paste this whole file into Extensions ▸ Apps Script in the club register spreadsheet, then save.
 * It is made from the files in src/ by "npm run bundle". To change it, change those files and bundle again.
 * Files: 00_Config.js, 01_Menu.js, 02_Helpers.js, 03_Setup.js, 04_Invoices.js, 05_Email.js, 06_Stripe.js, 07_Forms.js, 08_Calendar.js, 09_Builder.js, 10_Examples.js
 */
// ===== 00_Config.js =====

/**
 * CLUB REGISTER: configuration
 * ---------------------------------------------------------------------------
 * This file describes the tabs of the register and their columns, so that
 * "Club ▸ Admin ▸ Set up the register" builds them the same way every time.
 *
 * Prices, messages, bank details and test mode are NOT in the code. They live
 * in the Fees and Settings tabs, where the committee can see and change them.
 */

const TAB = {
  START: 'Start here',
  MEMBERS: 'Members',
  APPLICATIONS: 'Applications',
  BOATS: 'Boats',
  FEES: 'Fees',
  INVOICES: 'Invoices',
  LINES: 'Invoice lines',
  PAYMENTS: 'Payments',
  COURSES: 'Courses',
  BOOKINGS: 'Bookings',
  CALENDAR: 'Calendar',
  ROLES: 'Roles',
  SETTINGS: 'Settings',
  LOG: 'Log',
};

const ROLE = {
  ADMIN: 'Admin',
  TREASURER: 'Treasurer',
  MEMBERSHIP: 'Membership secretary',
  BOATPARK: 'Boat park manager',
  TRAINING: 'Training officer',
  EVENTS: 'Events lead',
};

/** Tab colours, one per role, so people can see at a glance whose tab it is. */
const ROLE_COLOUR = {
  [ROLE.ADMIN]: '#999999',
  [ROLE.TREASURER]: '#93c47d',
  [ROLE.MEMBERSHIP]: '#6fa8dc',
  [ROLE.BOATPARK]: '#f6b26b',
  [ROLE.TRAINING]: '#8e7cc3',
  [ROLE.EVENTS]: '#ffd966',
};

/** Dropdown choices. */
const LISTS = {
  memberStatus: ['Active', 'Applicant', 'Lapsed', 'Left'],
  feeCategory: ['Membership', 'Boat park', 'Other'],
  invoiceType: ['Membership', 'Boat park', 'Training', 'Other'],
  stage: ['Draft', 'Sent', 'Cancelled'],
  paymentMethod: ['Card (Stripe)', 'Bank transfer', 'Cash', 'Cheque', 'Refund'],
  boatStatus: ['In park', 'Waiting for space', 'Left'],
  decision: ['New', 'Accepted', 'Declined'],
  courseStatus: ['Open', 'Closed', 'Finished'],
  bookingStatus: ['Booked', 'Waiting list', 'Cancelled'],
  yesNo: ['Yes', 'No'],
  eventType: ['Racing', 'Training', 'Social', 'Work party', 'Meeting', 'Other'],
  eventStatus: ['Confirmed', 'Cancelled'],
};

/** Dropdowns whose choices come from a column of another tab. */
const LIST_RANGES = {
  membershipCodes: [TAB.FEES, 'Membership codes'],
  boatParkCodes: [TAB.FEES, 'Boat park codes'],
  memberLabels: [TAB.MEMBERS, 'Name and ID'],
  invoiceNos: [TAB.INVOICES, 'Invoice no.'],
  courseIds: [TAB.COURSES, 'Course ID'],
};

/**
 * The columns of each tab, left to right.
 *   h     the header. The code finds columns by their header, so columns can
 *         be moved, but headers must not be renamed.
 *   note  help shown when you hover over the header
 *   type  'date' | 'time' | 'money' | 'whole' | 'email' | 'text' (kept exactly as typed, e.g. 07700 phone numbers)
 *   list  a dropdown: a name from LISTS or LIST_RANGES
 *   f     a formula that fills the whole column automatically. These columns
 *         are grey and nobody types in them. c.L('Header') is a column on the
 *         same tab, c.R(tab, 'Header') a column on another tab.
 *   w     column width in pixels
 */
const SCHEMA = {
  [TAB.MEMBERS]: {
    owner: ROLE.MEMBERSHIP,
    about: 'Everyone in the club, one row per membership (a family is one row).',
    freezeColumns: 1,
    cols: [
      { h: 'Member ID', w: 90, note: 'Given automatically (M0001, M0002 …). Never change or reuse one.' },
      { h: 'First name', w: 110 },
      { h: 'Last name', w: 120 },
      { h: 'Email', w: 220, type: 'email', note: 'Invoices are emailed here.' },
      { h: 'Phone', w: 120, type: 'text' },
      { h: 'Address', w: 230 },
      { h: 'Postcode', w: 90 },
      { h: 'Membership', w: 200, list: 'membershipCodes', note: 'Which membership they pay for. The prices are on the Fees tab.' },
      {
        h: 'Status', w: 100, list: 'memberStatus',
        note: 'Active: gets an invoice in the yearly membership run.\n' +
          'Applicant: accepted, first invoice not paid yet. Becomes Active by itself when they pay.\n' +
          'Lapsed: did not renew.\nLeft: has left. Keep the row; never delete a member.',
      },
      { h: 'Joined', w: 100, type: 'date' },
      { h: 'Family members', w: 200, note: 'The other people covered by a family membership.' },
      { h: 'Notes', w: 260 },
      {
        h: 'Name and ID', w: 200, note: 'Worked out: the choices for the Owner dropdown on the Boats tab.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Member ID') + '="",,TRIM(' + c.L('First name') + '&" "&' + c.L('Last name') +
          ')&" ("&' + c.L('Member ID') + '&")"))',
      },
    ],
  },

  [TAB.APPLICATIONS]: {
    owner: ROLE.MEMBERSHIP,
    about: 'People who have filled in the join form.',
    cols: [
      { h: 'Received', w: 100, type: 'date' },
      { h: 'First name', w: 110 },
      { h: 'Last name', w: 120 },
      { h: 'Email', w: 220, type: 'email' },
      { h: 'Phone', w: 120, type: 'text' },
      { h: 'Address', w: 230 },
      { h: 'Postcode', w: 90 },
      { h: 'Membership wanted', w: 200, list: 'membershipCodes' },
      { h: 'About them', w: 260 },
      {
        h: 'Decision', w: 100, list: 'decision',
        note: 'New: waiting for a decision.\nTo accept: click the row, then Club ▸ Membership ▸ Accept selected application.\n' +
          'Declined: set this and let them know yourself.',
      },
      { h: 'Member ID', w: 90, note: 'Filled in when the application is accepted.' },
      { h: 'Notes', w: 240 },
    ],
  },

  [TAB.BOATS]: {
    owner: ROLE.BOATPARK,
    about: 'Every boat: owner, boat park space and which boat park charge applies.',
    freezeColumns: 1,
    cols: [
      { h: 'Boat ID', w: 80, note: 'Given automatically (B0001 …).' },
      { h: 'Owner', w: 200, list: 'memberLabels', note: 'Pick the owner from the list. Start typing a name to find them.' },
      { h: 'Boat name', w: 140 },
      { h: 'Class', w: 110 },
      { h: 'Sail number', w: 100, type: 'text' },
      { h: 'Space', w: 80, note: 'Where it is kept, e.g. C7. Blank if waiting for a space.' },
      { h: 'Boat park fee', w: 160, list: 'boatParkCodes', note: 'Which boat park charge applies. The prices are on the Fees tab.' },
      {
        h: 'Status', w: 130, list: 'boatStatus',
        note: 'In park: gets an invoice in the yearly boat park run.\nWaiting for space: not charged.\nLeft: has gone. Keep the row.',
      },
      { h: 'Insurance expiry', w: 110, type: 'date', note: 'Turns red once it has passed.' },
      { h: 'Notes', w: 240 },
    ],
  },

  [TAB.FEES]: {
    owner: ROLE.TREASURER,
    about: 'The price list: memberships, boat park and anything else the club charges for.',
    cols: [
      { h: 'Code', w: 220, note: 'The name used in the Members and Boats dropdowns. Once a code is in use, don\'t rename it: add a new one.' },
      { h: 'Category', w: 110, list: 'feeCategory' },
      { h: 'Description on invoice', w: 280 },
      { h: 'Amount £', w: 90, type: 'money' },
      { h: 'Notes', w: 240 },
      {
        h: 'Membership codes', w: 200, note: 'Worked out: the choices for the Membership dropdown on the Members tab.',
        f: (c) => 'IFERROR(FILTER(' + c.L('Code') + ',' + c.L('Category') + '="Membership"),"")',
      },
      {
        h: 'Boat park codes', w: 160, note: 'Worked out: the choices for the Boat park fee dropdown on the Boats tab.',
        f: (c) => 'IFERROR(FILTER(' + c.L('Code') + ',' + c.L('Category') + '="Boat park"),"")',
      },
    ],
  },

  [TAB.INVOICES]: {
    owner: ROLE.TREASURER,
    about: 'Every invoice, with what has been paid and what is still owed.',
    freezeColumns: 1,
    cols: [
      { h: 'Invoice no.', w: 100, note: 'Given automatically. Never change or delete an invoice: cancel it with Club ▸ Invoices ▸ Cancel selected invoice.' },
      { h: 'Date', w: 95, type: 'date' },
      { h: 'Bill to', w: 170 },
      { h: 'Type', w: 95, list: 'invoiceType' },
      { h: 'Year', w: 60, type: 'whole', note: 'The membership or boat park year the invoice is for.' },
      { h: 'Description', w: 230 },
      {
        h: 'Amount £', w: 90, type: 'money', note: 'Worked out: the total of this invoice\'s lines on the Invoice lines tab.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,SUMIF(' + c.R(TAB.LINES, 'Invoice no.') + ',' +
          c.L('Invoice no.') + ',' + c.R(TAB.LINES, 'Line £') + ')))',
      },
      {
        h: 'Paid £', w: 90, type: 'money', note: 'Worked out: the total of this invoice\'s payments on the Payments tab.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,SUMIF(' + c.R(TAB.PAYMENTS, 'Invoice no.') + ',' +
          c.L('Invoice no.') + ',' + c.R(TAB.PAYMENTS, 'Amount £') + ')))',
      },
      {
        h: 'Balance £', w: 90, type: 'money', note: 'Worked out: Amount minus Paid (0 if cancelled).',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,IF(' + c.L('Stage') + '="Cancelled",0,ROUND(' +
          c.L('Amount £') + '-' + c.L('Paid £') + ',2))))',
      },
      {
        h: 'Status', w: 90,
        note: 'Worked out:\nDraft: not emailed yet.\nUnpaid: emailed, not due yet.\nOverdue: past the due date.\n' +
          'Part paid, Paid, Overpaid: from the Payments tab.\nCancelled: cancelled with the Club menu.',
        f: (c) => {
          const stage = c.L('Stage'), bal = c.L('Balance £'), paid = c.L('Paid £'), due = c.L('Due');
          return 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,IF(' + stage + '="Cancelled","Cancelled",IF(' + stage +
            '="Draft","Draft",IF(' + bal + '<0,"Overpaid",IF(' + bal + '=0,"Paid",IF(' + paid + '>0,"Part paid",IF(' + due +
            '<TODAY(),"Overdue","Unpaid"))))))))';
        },
      },
      { h: 'Due', w: 95, type: 'date' },
      { h: 'Member ID', w: 90 },
      { h: 'Email', w: 210, type: 'email' },
      { h: 'Stage', w: 85, list: 'stage', note: 'Draft until it has been emailed. Changed by the Club menu, not by hand.' },
      { h: 'Sent on', w: 95, type: 'date' },
      { h: 'Reminders', w: 85, type: 'whole' },
      { h: 'Last reminder', w: 100, type: 'date' },
      { h: 'Pay link', w: 160, note: 'The Stripe page where this invoice can be paid by card.' },
      { h: 'Link £', w: 80, type: 'money', note: 'How much the pay link charges.' },
      { h: 'Stripe link ID', w: 140 },
      { h: 'Notes', w: 240 },
    ],
  },

  [TAB.LINES]: {
    owner: ROLE.TREASURER,
    about: 'What each invoice is for, line by line.',
    cols: [
      { h: 'Invoice no.', w: 100 },
      { h: 'Description', w: 340 },
      { h: 'Code', w: 170, note: 'The fee code, when the line came from the Fees tab.' },
      { h: 'Qty', w: 50, type: 'whole' },
      { h: 'Unit £', w: 80, type: 'money' },
      {
        h: 'Line £', w: 80, type: 'money', note: 'Worked out: Qty × Unit £.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,ROUND(' + c.L('Qty') + '*' + c.L('Unit £') + ',2)))',
      },
      { h: 'Ref', w: 90, note: 'The member, boat or course this line is for. Stops anyone being charged twice.' },
    ],
  },

  [TAB.PAYMENTS]: {
    owner: ROLE.TREASURER,
    about: 'Every payment. Card payments appear by themselves; bank payments are typed in.',
    cols: [
      { h: 'Date', w: 95, type: 'date' },
      { h: 'Invoice no.', w: 100, list: 'invoiceNos' },
      { h: 'Amount £', w: 90, type: 'money', note: 'For a refund, type a minus amount, e.g. -25.' },
      { h: 'Method', w: 120, list: 'paymentMethod' },
      {
        h: 'Bill to', w: 170, note: 'Worked out from the invoice number.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,IFERROR(VLOOKUP(' + c.L('Invoice no.') + ',{' +
          c.R(TAB.INVOICES, 'Invoice no.') + ',' + c.R(TAB.INVOICES, 'Bill to') + '},2,FALSE),"No such invoice")))',
      },
      { h: 'Reference', w: 190, note: 'Bank reference, cheque number, or the payer\'s email for card payments.' },
      { h: 'Recorded by', w: 150 },
      { h: 'Stripe ID', w: 160, note: 'Filled in for card payments. Stops the same payment being recorded twice.' },
      { h: 'Notes', w: 240 },
    ],
  },

  [TAB.COURSES]: {
    owner: ROLE.TRAINING,
    about: 'Training courses: dates, places and prices.',
    cols: [
      { h: 'Course ID', w: 85, note: 'Given automatically (C001 …).' },
      { h: 'Course', w: 230 },
      { h: 'Starts', w: 95, type: 'date' },
      { h: 'Ends', w: 95, type: 'date' },
      { h: 'Places', w: 70, type: 'whole' },
      { h: 'Price £', w: 80, type: 'money' },
      {
        h: 'Booked', w: 70, type: 'whole', note: 'Worked out: bookings with status Booked.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Course ID') + '="",,COUNTIFS(' + c.R(TAB.BOOKINGS, 'Course ID') + ',' +
          c.L('Course ID') + ',' + c.R(TAB.BOOKINGS, 'Status') + ',"Booked")))',
      },
      {
        h: 'Places left', w: 85, type: 'whole', note: 'Worked out: Places minus Booked.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Course ID') + '="",,' + c.L('Places') + '-' + c.L('Booked') + '))',
      },
      { h: 'Status', w: 90, list: 'courseStatus', note: 'Open courses with places left appear on the booking form.' },
      { h: 'Notes', w: 240 },
    ],
  },

  [TAB.BOOKINGS]: {
    owner: ROLE.TRAINING,
    about: 'Course bookings, mostly from the booking form.',
    cols: [
      { h: 'Received', w: 95, type: 'date' },
      { h: 'Course ID', w: 85, list: 'courseIds' },
      {
        h: 'Course', w: 200, note: 'Worked out from the Course ID.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Course ID') + '="",,IFERROR(VLOOKUP(' + c.L('Course ID') + ',{' +
          c.R(TAB.COURSES, 'Course ID') + ',' + c.R(TAB.COURSES, 'Course') + '},2,FALSE),"No such course")))',
      },
      { h: 'Participant', w: 160 },
      { h: 'Under 18', w: 75, list: 'yesNo' },
      { h: 'Booked by', w: 160 },
      { h: 'Email', w: 210, type: 'email' },
      { h: 'Phone', w: 120, type: 'text' },
      { h: 'Status', w: 105, list: 'bookingStatus' },
      { h: 'Invoice no.', w: 100 },
      {
        h: 'Payment', w: 90, note: 'Worked out from the invoice.',
        f: (c) => 'ARRAYFORMULA(IF(' + c.L('Invoice no.') + '="",,IFERROR(VLOOKUP(' + c.L('Invoice no.') + ',{' +
          c.R(TAB.INVOICES, 'Invoice no.') + ',' + c.R(TAB.INVOICES, 'Status') + '},2,FALSE),"")))',
      },
      { h: 'Medical / notes', w: 260 },
    ],
  },

  [TAB.CALENDAR]: {
    owner: ROLE.EVENTS,
    about: 'The club calendar. Club ▸ Calendar ▸ Publish the calendar copies it to Google Calendar.',
    cols: [
      { h: 'Date', w: 95, type: 'date' },
      { h: 'Start', w: 65, type: 'time', note: 'Like 09:30. Leave blank for an all-day event.' },
      { h: 'End', w: 65, type: 'time' },
      { h: 'Event', w: 230 },
      { h: 'Where', w: 140 },
      { h: 'Details', w: 260 },
      { h: 'Type', w: 100, list: 'eventType' },
      { h: 'Status', w: 100, list: 'eventStatus', note: 'To take an event off the calendar, set Cancelled and publish. Don\'t delete the row first.' },
      { h: 'On calendar', w: 140, note: 'Filled in when the calendar is published.' },
      { h: 'Event ID', w: 120, note: 'Filled in automatically. Links the row to its Google Calendar event.' },
    ],
  },

  [TAB.ROLES]: {
    owner: ROLE.ADMIN,
    about: 'Who does which job. Used for notification emails and for locking tabs.',
    cols: [
      { h: 'Role', w: 180 },
      { h: 'Name', w: 160 },
      { h: 'Email', w: 220, type: 'email' },
      { h: 'Looks after', w: 420 },
    ],
  },

  [TAB.SETTINGS]: {
    owner: ROLE.ADMIN,
    about: 'Club name, invoice numbering, test mode, bank details and email wording.',
    cols: [
      { h: 'Setting', w: 230 },
      { h: 'Value', w: 300 },
      { h: 'What it does', w: 520 },
    ],
  },

  [TAB.LOG]: {
    owner: ROLE.ADMIN,
    about: 'A record of everything the Club menu and the automatic checks have done.',
    cols: [
      { h: 'When', w: 140 },
      { h: 'Who', w: 190 },
      { h: 'What', w: 230 },
      { h: 'Details', w: 600 },
    ],
  },
};

/** The order the tabs appear in. */
const TAB_ORDER = [
  TAB.START, TAB.MEMBERS, TAB.APPLICATIONS, TAB.BOATS, TAB.FEES, TAB.INVOICES, TAB.LINES,
  TAB.PAYMENTS, TAB.COURSES, TAB.BOOKINGS, TAB.CALENDAR, TAB.ROLES, TAB.SETTINGS, TAB.LOG,
];

/**
 * The Settings tab: [name, default, what it does].
 * A default of null is filled in when the register is set up.
 */
const SETTINGS_LIST = [
  ['Club name', null, 'Shown on invoices and emails.'],
  ['Club email', '', 'Replies to invoice emails go here. Blank: replies go to the account that sends them.'],
  ['Invoice prefix', 'INV-', 'Goes in front of every invoice number.'],
  ['First invoice number', 1001, 'The number the first invoice gets. Set it to follow on from your old system.'],
  ['Membership year', null, 'The year the membership and boat park runs suggest.'],
  ['Payment terms (days)', 30, 'How long people have to pay.'],
  ['Test mode', 'Yes', 'While this says Yes, every email goes to the test address below, not to members. Set it to No when you go live.'],
  ['Test email address', null, 'Where emails go while test mode is on.'],
  ['Show bank details on invoices', 'No', 'Card payment through Stripe is the normal way to pay. Yes also shows your bank details. Bank payments have to be checked and typed in by hand.'],
  ['Bank account name', '', 'Only used if bank details are shown.'],
  ['Sort code', '', ''],
  ['Account number', '', ''],
  ['Attach PDF invoice', 'Yes', 'Attach a PDF copy of the invoice to the email.'],
  ['Days between reminders', 14, 'Send reminders skips anyone reminded more recently than this.'],
  ['Invoice message', 'Thank you for being part of the club. Your invoice is below.', 'The first paragraph of every invoice email.'],
  ['Reminder message', 'Our records show this invoice has not been paid yet. If you have paid in the last few days, thank you, and please ignore this email.', 'The first paragraph of every reminder.'],
  ['Email footer', '', 'Small print at the bottom of every email, e.g. the club address.'],
  ['Privacy notice link', '', 'A link to your privacy notice. It is shown on the join and booking forms.'],
  ['Invoice course bookings automatically', 'Yes', 'Yes: when someone books a course with a place free, they are emailed an invoice straight away.'],
  ['Google Calendar ID', '', 'Filled in the first time the calendar is published.'],
  ['Join form', '', 'Filled in by Club ▸ Admin ▸ Create the forms. Put this link on the website.'],
  ['Booking form', '', 'Filled in by Club ▸ Admin ▸ Create the forms. Put this link on the website.'],
  ['Boat form', '', 'Filled in by Club ▸ Admin ▸ Create the forms. Put this link on the website.'],
  ['Stripe', 'Not connected', 'Filled in automatically.'],
  ['Last payment check', '', 'Filled in automatically.'],
];

/** The Roles tab as it starts out: [role, looks after]. */
const ROLES_LIST = [
  [ROLE.ADMIN, 'Settings, Roles, the Stripe connection and the script. Two people should hold this role. Header rows can only be changed by Admins.'],
  [ROLE.ADMIN, ''],
  [ROLE.TREASURER, 'Fees, Invoices, Invoice lines, Payments. Makes and sends invoices, records bank payments.'],
  [ROLE.MEMBERSHIP, 'Members, Applications. Accepts new members, marks leavers.'],
  [ROLE.BOATPARK, 'Boats. Spaces, new and departing boats.'],
  [ROLE.TRAINING, 'Courses, Bookings.'],
  [ROLE.EVENTS, 'Calendar.'],
];

/** Gmail's daily limit is shared with everything else the account sends, so keep a few spare. */
const EMAIL_SPARE = 2;

/** Stop a long run before Google's 6-minute limit, so it can finish cleanly and be run again. */
const TIME_BUDGET_MS = 4.5 * 60 * 1000;

// ===== 01_Menu.js =====

/**
 * CLUB REGISTER: the Club menu
 * ---------------------------------------------------------------------------
 * Google runs onOpen() every time someone opens the spreadsheet. It adds the
 * Club menu. Each menu item calls one of the functions named below.
 */

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('Club')
    .addItem('Go to Start here', 'goToStart')
    .addSeparator()
    .addSubMenu(ui.createMenu('Membership')
      .addItem('Prepare membership invoices…', 'prepareMembershipInvoices')
      .addItem('Accept selected application', 'acceptSelectedApplications'))
    .addSubMenu(ui.createMenu('Boat park')
      .addItem('Prepare boat park invoices…', 'prepareBoatParkInvoices'))
    .addSubMenu(ui.createMenu('Invoices')
      .addItem('New invoice…', 'openInvoiceBuilder')
      .addItem('Send draft invoices…', 'sendDraftInvoices')
      .addItem('Send reminders…', 'sendReminders')
      .addItem('Cancel selected invoice…', 'cancelSelectedInvoice'))
    .addSubMenu(ui.createMenu('Payments')
      .addItem('Update payments now', 'updatePaymentsNow'))
    .addSubMenu(ui.createMenu('Training')
      .addItem('Invoice selected booking', 'invoiceSelectedBookings')
      .addItem('Update the booking form', 'updateForms'))
    .addSubMenu(ui.createMenu('Calendar')
      .addItem('Publish the calendar', 'publishCalendar'))
    .addSeparator()
    .addSubMenu(ui.createMenu('Admin')
      .addItem('Set up the register', 'setUpRegister')
      .addItem('Add example data', 'addExampleData')
      .addItem('Connect Stripe…', 'connectStripe')
      .addItem('Turn on automatic payment checks', 'turnOnPaymentChecks')
      .addItem('Create the forms', 'createForms')
      .addItem('Update the forms', 'updateForms')
      .addItem('Lock tabs to their roles', 'lockTabs'))
    .addToUi();
}

function goToStart() {
  const sheet = ss_().getSheetByName(TAB.START);
  if (sheet) sheet.activate();
  else alert_('Club register', 'There is no Start here tab yet. Use Club ▸ Admin ▸ Set up the register.');
}

// ===== 02_Helpers.js =====

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

// ===== 03_Setup.js =====

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

// ===== 04_Invoices.js =====

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

// ===== 05_Email.js =====

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

// ===== 06_Stripe.js =====

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

// ===== 07_Forms.js =====

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

// ===== 08_Calendar.js =====

/**
 * CLUB REGISTER: the calendar
 * ---------------------------------------------------------------------------
 * The season is planned as a list on the Calendar tab (easy to copy from last
 * year). Club ▸ Calendar ▸ Publish the calendar copies it to a Google Calendar
 * that the website can show and members can add to their phones.
 *
 * Each row remembers its Google Calendar event (Event ID), so publishing again
 * updates events rather than duplicating them. To remove an event, set its
 * Status to Cancelled and publish.
 */

function publishCalendar() {
  withUi_('Publish the calendar', () => {
    const s = settings_();
    const tz = tz_();
    let calendar = null;
    const calendarId = String(s['Google Calendar ID'] || '').trim();
    if (calendarId) calendar = CalendarApp.getCalendarById(calendarId);
    if (!calendar) {
      if (!confirm_('Publish the calendar', 'There is no club Google Calendar yet. Make one now, called "' + s['Club name'] + ' calendar"?')) return;
      calendar = CalendarApp.createCalendar(String(s['Club name'] || 'Club') + ' calendar', { timeZone: 'Europe/London' });
      setSetting_('Google Calendar ID', calendar.getId());
    }

    const t = table_(TAB.CALENDAR);
    const shown = t.rows.length ? t.sheet.getRange(2, 1, t.rows.length, t.headers.length).getDisplayValues() : [];
    const stamp = 'Published ' + Utilities.formatDate(new Date(), tz, 'dd/MM/yyyy HH:mm');
    let added = 0;
    let changed = 0;
    let removed = 0;
    const problems = [];

    t.rows.forEach((r, i) => {
      const title = String(r.get('Event')).trim();
      const date = r.get('Date');
      if (!title && !(date instanceof Date)) return;
      if (!title || !(date instanceof Date)) {
        problems.push('Row ' + r.n + ': needs both a date and an event name');
        return;
      }
      const id = String(r.get('Event ID')).trim();
      let event = null;
      if (id) {
        try {
          event = calendar.getEventById(id);
        } catch (e) {
          event = null;
        }
      }

      if (String(r.get('Status')).trim() === 'Cancelled') {
        if (event) {
          event.deleteEvent();
          removed++;
        }
        if (id) r.set('Event ID', '');
        if (String(r.get('On calendar')).indexOf('Removed') !== 0) r.set('On calendar', 'Removed ' + stamp.slice(10));
        return;
      }

      const day = Utilities.formatDate(date, tz, 'yyyy-MM-dd');
      const startText = timeText_(shown[i][t.col['Start']]);
      const endText = timeText_(shown[i][t.col['End']]);
      if (shown[i][t.col['Start']] && !startText) problems.push('Row ' + r.n + ': start time "' + shown[i][t.col['Start']] + '" not understood');
      const details = { location: String(r.get('Where')).trim(), description: String(r.get('Details')).trim() };

      if (!startText) {
        const noon = Utilities.parseDate(day + ' 12:00', tz, 'yyyy-MM-dd HH:mm'); // the day itself, whatever the time zone
        if (event && event.isAllDayEvent()) {
          event.setAllDayDate(noon);
        } else {
          if (event) event.deleteEvent();
          event = calendar.createAllDayEvent(title, noon, details);
          r.set('Event ID', event.getId());
          if (!id) added++;
        }
      } else {
        const start = Utilities.parseDate(day + ' ' + startText, tz, 'yyyy-MM-dd HH:mm');
        let end = endText ? Utilities.parseDate(day + ' ' + endText, tz, 'yyyy-MM-dd HH:mm') : new Date(start.getTime() + 60 * 60000);
        if (end.getTime() <= start.getTime()) end = new Date(start.getTime() + 60 * 60000);
        if (event && !event.isAllDayEvent()) {
          event.setTime(start, end);
        } else {
          if (event) event.deleteEvent();
          event = calendar.createEvent(title, start, end, details);
          r.set('Event ID', event.getId());
          if (!id) added++;
        }
      }
      if (id) {
        event.setTitle(title);
        event.setLocation(details.location);
        event.setDescription(details.description);
        changed++;
      }
      r.set('On calendar', stamp);
    });

    log_('Calendar published', added + ' added, ' + changed + ' updated, ' + removed + ' removed' +
      (problems.length ? '. Check: ' + problems.join(' | ') : ''));
    alert_('Publish the calendar', 'Done: ' + added + ' added, ' + changed + ' updated, ' + removed + ' removed.' +
      (problems.length ? '\n\nPlease check:\n' + listText_(problems) : '') +
      '\n\nTo show the calendar on the website, open Google Calendar, find "' + calendar.getName() +
      '", choose Settings and sharing, tick "Make available to public", and copy the embed code.');
  });
}

/** "9.30", "09:30" or "0930" → "09:30"; anything else → "". */
function timeText_(shown) {
  const m = String(shown || '').trim().match(/^(\d{1,2})[:.]?(\d{2})$/);
  if (!m) return '';
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return '';
  return String(h).padStart(2, '0') + ':' + String(min).padStart(2, '0');
}

// ===== 09_Builder.js =====

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

// ===== 10_Examples.js =====

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
