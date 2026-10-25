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
