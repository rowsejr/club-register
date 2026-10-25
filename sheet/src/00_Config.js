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
