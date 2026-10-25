# The committee's Sheet

A Google Sheet that holds a read-only copy of lists from the club office, refreshed every hour:

| Tab | What it shows |
|---|---|
| Who owes | Everyone who owes money, longest late first, with the last reminder and the latest note |
| Payments | Money received in the last 13 months |
| Members | Members with their number, membership, status and whether they have paid. **No** emails, phones or addresses |
| Bookings | Places booked on coming events, and whether they are paid |
| RYA census | People covered by a membership, by age band and gender |
| Money summary | What is owed, what has come in this month and this year, how many members |
| Coming events | Dates, prices, places left and the booking link |
| Boats | Boat, class, sail number, Portsmouth number and owner |

Nobody edits the Sheet. Every tab is protected, and anything typed in is replaced at the next refresh. To change
anything, use the club office.

## Set it up (about 10 minutes, once)

You need: the club office address, and the **sheet key**. The office administrator sets the key on the server
(`CLUB_FEED_KEYS="sheet:<a long random secret>"`) and gives it to you privately.

1. Signed in to Google as **the club's own account** (not a personal one), open [sheets.new](https://sheets.new).
   Name the spreadsheet, for example *Club office copy*.
2. Choose **Extensions ▸ Apps Script**. Delete what is in the editor and paste in the whole of [`Copy.gs`](Copy.gs).
   Press **Save**.
3. Still in Apps Script, choose **Project Settings** (the cog), tick **Show "appsscript.json" manifest file**, open
   `appsscript.json` in the editor and replace it with [`appsscript.json`](appsscript.json). Press **Save**.
4. Go back to the spreadsheet and reload it. A **Club office** menu appears after a few seconds.
5. Choose **Club office ▸ Set up**. Google asks for permission the first time: **Continue**, pick the club account,
   then **Advanced ▸ Go to (unsafe) ▸ Allow**. Google says "unsafe" only because this is the club's own script, not a
   published app. Enter the office address and the sheet key when asked.
6. The tabs fill in. Share the spreadsheet with the committee as **Viewers**.

If a refresh fails (the office is down, or the key was changed), Google emails the club account. **Club office ▸
Refresh now** tries again straight away.

## How it stays safe

- The key only lets the Sheet **read** these lists. It cannot change anything in the office.
- Each request is signed and stamped with the time; the office refuses old, repeated or wrongly signed requests.
- The members list has no contact details, so the Sheet can be shared with the whole committee.
- To stop the copy, the office administrator removes the sheet key from the server.

## Tests

`node --test "sheet/tests/*.test.js"` checks the Sheet signs requests exactly as the office expects (the same example is
in `tests/test_feeds.py`) and that a list lands in a protected tab.
