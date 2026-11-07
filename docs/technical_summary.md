# Beans&Beats — Technical Summary (Submission)

Christmas Together 2026 · 26–29 Nov · Hall F, Marina Bay Sands · Live: https://165-22-242-253.sslip.io · Ready board: /board · All profits to Singapore Red Cross Young Hearts

[[column]]

## How it fits together

[[diagram]]

- **Order** on one tablet or laptop · **Queue** on a laptop (number keys) with a **second screen** for the Ready board · **Pickup** on any device with a camera
- Guests use **their own phone**: scan the QR, no app, no login
- One person can run it all on one device; more staff = more devices, kept in sync

## Stack and architecture

| Part | Built with |
| --- | --- |
| Staff app, guest page, board | React 19 + Vite, any browser |
| Works offline | Service worker + IndexedDB (Dexie) |
| Server | FastAPI, Python 3.14 |
| Database | PostgreSQL 17 |
| Live updates | Server-sent events; polling as backup |
| Hosting | Docker + Caddy HTTPS, DigitalOcean Singapore |
| Access | Barista / Booth Lead / Admin, checked by the server |

- One live connection per page; pages re-check every 5 s (guest pages 20 s) if push drops
- Every change is saved with an audit entry in the same database step

[[column]]

## Offline and sync

1. Every tap (order, Making, Ready, Collected, Cancel) is **saved on the device first**
2. When online, the device sends them **one at a time, in order**, to `POST /api/sync`
3. Each action has a **unique ID**: a resend is never counted twice
4. Status **only moves forward**; a manager's correction is never undone by an old offline tap
5. Each device has **its own letter** (A001, B001), so offline numbers never clash
6. Other devices, guest phones and the board update **within about 2 s** of the sync

- Keeps working through a Wi-Fi drop; reloading the page offline keeps every saved order
- Needs a connection: sign-in, booth settings, reports, CSV export, day reset

## Personal data and PDPA

| | |
| --- | --- |
| ✓ | **First name only**, used to call the guest and show on the board |
| ✗ | No phone, email, address or payment details |
| ✗ | No cookies, analytics or tracking on guest pages |
| ✓ | Guest page reachable only by its own **random link** |
| ✓ | Live updates sent to phones carry **no names** |
| ✓ | **Day reset clears that day's names**; sales and audit stay without names |
| ✓ | Only signed-in staff see names; the audit trail never stores them |
| ✓ | Payments stay on the organiser's POS |
| ! | Daily backups (kept 14 days) may hold names from before a reset |

## Known limitations

- Devices sync through the internet; a booth-only network (no internet) is not built
- An offline device's changes reach others only after it reconnects
- Guest sound needs one tap first, and the page must stay open
- Staff devices and camera scanning need the HTTPS link
- Not yet tested at the hall: board reading at 3–5 m, every phone model, a 10-hour day
- Audit trail is locked in the app, not in the database itself
- Change the test logins before the event

[[column]]

## The six core modules

| Module | What it does |
| --- | --- |
| 1 Order screen | Tap drink → Hot/Iced → Dairy/Oat → **Add to order** → first name → **Place order**. Number A001…, total to key into the POS, QR for the guest |
| 2 Barista queue | Oldest first. **One tap Making, one tap Ready** (or type **012 + Enter**). Tabs: Active, To make, Received, Making, Ready, Late, Completed, Cancelled |
| 3 Guest status page | Scan QR → order number, drinks, progress steps, orders ahead, **wait estimate**; chime, flash and title change when Ready |
| 4 Ready board | Second screen / TV: **12 Ready + 12 In progress**, number beside name, pages every 8 s; Ready on the left or right |
| 5 Menu controls | Change items and prices, **sold out** for a drink or oat milk; every device updates live |
| 6 Daily sales report | Cups and totals **per day, by drink and option**; **CSV** to match the organiser's POS; cancelled, refunded and discarded counts |

## Extras

- **Pickup scan:** the guest's QR is the pickup ticket; the device camera reads it and marks Collected, with **Undo** for 10 s (or a Hand over tap)
- **Waiting-time estimate:** "Usually ready in about 7–11 min" from today's real pace; on/off for the board and for phones
- **Charity counter** panel on the board: cups today or all days, optional sales amount
- **Late drinks:** orange after 30 min, red after 60, guest warned; **Discard** button
- **Cancel** with a reason and refund status; **Cancelled** tab; Cancel in the popup and/or on the ticket
- **Booth settings (Booth Lead):** skip Making, collect on scan, reminder times, board side
- **Audit trail** (Admin), settings-change notice on other devices, **personal staff logins**

[[column]]

## Why it stands out

| | |
| --- | --- |
| No app, no hardware | Any browser; the camera reads the QR; no printer or reader |
| Wi-Fi drops? Keeps going | Saved on the device, synced once |
| Luckin-style guest alert | Live status, real wait estimate, chime |
| Total matches the POS | Refunds recorded; report counts only paid cups |
| Handles the crowd | 300 phones, 10 min: 95% under 10 ms, 0 errors |
| Any crew size | One device or many, in sync |
| Safe by default | Server-checked roles, rate-limited logins, HTTPS, daily backups |

## How a drink flows

1. **Order:** barista keys it in; guest pays at the POS and scans the QR
2. **Queue:** Start making → Mark ready
3. **Guest phone:** chimes; name appears on the Ready board
4. **Pickup:** scan the guest's QR → Collected, off the board
5. **End of day:** Booth Lead checks every device is Synced → **Reset day**: names cleared, report kept

## Tested

Latest backend checks: 91 tests passed on both SQLite and PostgreSQL (7 November).

- All 20 browser tests passed (7 Nov), including Wi-Fi drop and recovery
- Load test: 300 guest phones for 10 minutes on a 1-CPU server, no errors
- Every acceptance test is logged with its evidence
