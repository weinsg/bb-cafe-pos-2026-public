# Beans&Beats Booth — Submission Memo

## What you built

**Beans&Beats Booth: an order and pickup system for the charity coffee booth at Christmas Together 2026** (26–29 Nov, Hall F, Marina Bay Sands; all profits to Singapore Red Cross Young Hearts).

It replaces shouting names and paper tickets with a Luckin-style flow:

- A barista takes the order on a **tablet or laptop**: drink, hot or iced, dairy or oat, and the guest's **first name**. Each order gets a number such as A012.
- The guest **scans a QR code with their own phone**. No app, no sign-up. They see the drink's progress, how many orders are ahead and a **waiting-time estimate**, and their phone **chimes when it's ready**.
- Baristas work a **live queue**: one tap for Making, one tap for Ready, or type the 3-digit number on a number pad and press Enter (a laptop needs a small USB number pad).
- A **ready board** on a second screen shows the numbers and names ready to collect.
- At pickup, staff **scan the guest's QR with the device camera**; no QR reader or printer needed.
- The Booth Lead can **change the menu, prices and sold-out items** live, and the **Report** report tallies sales, cancels and refunds for a day or all event days, with five CSV exports and a final recorded total for reconciliation with the organiser's POS.

All six modules in the brief are built and live at https://165-22-242-253.sslip.io. Extras:

- waiting-time estimate;
- charity counter on the board (top bar or a left/right side panel);
- reminders for uncollected drinks (orange, then red, then Discard);
- cancel with refund tracking;
- live booth settings;
- an audit trail and personal staff logins.

## How it works

1. **Ordering:** the barista's device saves every order and every tap **on the device first**, then sends them to the server. If the hall Wi-Fi drops, the booth keeps working, and everything syncs once the connection returns, each action exactly once. Each device has its own number letter (A, B…), so offline numbers never clash.
2. **Live updates:** when an order changes, the server pushes the change to every staff device, the guest's phone and the ready board within about 2 seconds. Guest phones only reload for their own order, which keeps the server light under crowds.
3. **Pickup:** the guest's QR is their ticket. Scanning it marks the order Collected (with a 10-second Undo) and removes it from the board.
4. **End of day:** the Booth Lead runs **Reset day**, which clears that day's first names (PDPA) and keeps sales figures and the audit trail.

**Tools and stack:** React 19 web app (works in any browser and offline through a service worker and IndexedDB), FastAPI on Python 3.14, PostgreSQL 17, live updates with server-sent events, Docker, and Caddy for automatic HTTPS. It's hosted on a DigitalOcean server in Singapore, with automatic deploys from GitHub after the tests pass, and daily database backups.

**Data:** only the guest's first name. No phone numbers, emails, payment details, cookies or analytics. The menu and prices are the owner's real menu. All testing used made-up orders and a booth simulator.

## Baseline results (7 November 2026)

- **All 6 required modules live**, plus 8 extras.
- **99 automated backend tests** pass on both SQLite and PostgreSQL.
- **25 automated browser tests** pass, covering the full order flow, a Wi-Fi drop and recovery, pickup scanning, cancel, the ready board, the sales report and phone screens.
- **211 written acceptance tests:** 169 fully passed; 13 passed in testing with only a real-device check left; 26 waiting on real phones, tablets or the hall itself; 3 still open (two need a working connection between booth devices without internet, and one is the 300-phone test on the live server).
- **Load:** 300 guest phones checking for 10 minutes on a 1-CPU test server. 95% of answers came within **10 ms**, with **0 errors**. Before the fix, the old design took **8.4 s** in the same test and **25.6 s** on the live server. Even with live push down on all 300 phones, answers stayed under **0.3 s**.
- **Speed at the counter:** an order takes a few taps, marking Ready takes 1 tap (or 3 digits + Enter), and the queue shows **10 tickets on one tablet screen** without scrolling.
- **POS reconciliation:** staff record refund status so the final recorded total can be compared with the organiser's POS; actual POS transactions are not independently verified.

## Limitations and next steps

**Not done yet:**

- **Devices sync through the internet.** A device that goes offline keeps working, but other devices see its orders only after it reconnects.
- **Not yet tested in the hall:** board readability at 3–5 m, every phone model, and a full 10-hour day.
- **Guest alert:** the phone must keep the page open, and sound needs one tap first (a browser rule).
- **Audit trail:** it can't be edited through the app, but a database administrator could still change it.
- **Server size:** the live server has 1 CPU. The 300-phone load test passed on a 1-CPU test server, but not yet on the live one.

**Next steps (hardening week, 9–13 Nov):**

1. **Lock the audit trail in the database itself.**
2. **New logins** for the event and a short **staff cheat-sheet** for the booth.
