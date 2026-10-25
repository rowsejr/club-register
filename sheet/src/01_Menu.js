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
