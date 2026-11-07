# Beans&Beats × Christmas Together 2026: Coffee Booth Order and Pickup System

| | |
| --- | --- |
| **Live app** | https://165-22-242-253.sslip.io |
| **Ready board** | https://165-22-242-253.sslip.io/board |
| **Test logins** | Given in the submission form (not published here) |
| **Demo video (3 min)** | DEMO_VIDEO_LINK |
| **Technical summary** | [technical_summary.pdf](docs/technical_summary.pdf): features, architecture, offline sync, PDPA and known limitations |

Order and pickup system for the Beans&Beats charity coffee booth at Christmas Together 2026 (26–29 November, Hall F, Marina Bay Sands). Baristas take orders on a tablet or laptop, work a live queue, and guests follow their drink on their own phone, with an alert when it is ready. Payment stays on the organiser's POS. All profits go to Singapore Red Cross Young Hearts.

This README is the step-by-step guide to install, deploy and run the system. What every screen does is in the [technical summary](docs/technical_summary.pdf).

## Contents

1. [What you need](#1-what-you-need)
2. [Install and try it on your computer](#2-install-and-try-it-on-your-computer)
3. [Deploy to a server (live link)](#3-deploy-to-a-server-live-link)
4. [Set up the booth devices](#4-set-up-the-booth-devices)
5. [Reset between event days](#5-reset-between-event-days)
6. [Backups](#6-backups)
7. [Run the tests](#7-run-the-tests)
8. [FAQ](#8-faq)

## 1. What you need

**To install:** [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on Windows or Mac) and [Git](https://git-scm.com/downloads). No GitHub account or SSH key is needed: the repository is cloned over HTTPS.

**At the booth** (all in a web browser; nothing to install):

| Station | Device | Screen |
| --- | --- | --- |
| Order | One tablet or laptop | **New order** |
| Queue | A laptop with a small **USB number pad** (most laptops have none), plus a **second screen** facing the guests | Laptop: **Queue**. Second screen: the **Ready board** (`/board`) |
| Pickup | A tablet, laptop or phone with a camera | **Pickup** (the camera reads the guest's QR; no QR reader needed) |
| Guests | Their own phone | The page they open by scanning the QR |

One person can run every station on one device.

## 2. Install and try it on your computer

1. **Get the code:**
   ```bash
   git clone https://github.com/weinsg/bb-cafe-pos-2026-public.git
   cd bb-cafe-pos-2026-public
   ```
2. **Create your settings file** from the example:
   ```bash
   cp .env.example .env
   ```
3. **Fill in `.env`** with your own values (make each random value with `openssl rand -hex 32`):
   - `SECRET_KEY`: a random value, at least 32 characters
   - `POSTGRES_PASSWORD`: a random value for the database
   - `ADMIN_PASSWORD`: the password for the starting **admin** login
   - Optional: `BARISTA_PASSWORD` and `BOOTH_LEAD_PASSWORD` create a **barista** and a **booth_lead** login on first start

   `.env` is never committed. No password is written anywhere in this repository.
4. **Start it:**
   ```bash
   docker compose up --build -d
   ```
   The first build takes a few minutes. If a value is missing, it stops and tells you which one.
5. **Open it:**

   | Page | Address |
   | --- | --- |
   | Staff sign-in | http://localhost:8000/ |
   | Ready board | http://localhost:8000/board |
   | API documentation | http://localhost:8000/docs |

6. **Sign in** as `admin` with your `ADMIN_PASSWORD`.
7. **Add staff logins** (the password is typed in, never stored in a file); the role is `barista`, `booth_lead` or `admin`:
   ```bash
   docker compose exec app python -m app.manage add-staff <username> <role>
   docker compose exec app python -m app.manage list-staff
   ```
8. **Try one order:** **New order** → tap a drink → Hot/Iced → Dairy/Oat → **Add to order** → type a first name → **Place order** → **Open guest status** (the guest's page) → **Queue**: **Start making**, **Mark ready** → watch the guest page and `/board` update.
9. **Stop it** (your data is kept): `docker compose down`. Never use `docker compose down -v`: it deletes the database.

To open a guest QR on a phone, the phone must reach the app's address: set `GUEST_BASE_URL` in `.env` to an HTTPS address the phone can open, then run `docker compose up -d` again.

## 3. Deploy to a server (live link)

Staff devices and phone cameras need **HTTPS**. `docker-compose.prod.yml` runs the app, the database and Caddy, which gets the HTTPS certificate automatically.

1. **Create a server:** Ubuntu 24.04 with at least 1 GB RAM (2 vCPU recommended for a busy event), close to your guests (e.g. Singapore).
2. **Install Docker** on it: follow https://docs.docker.com/engine/install/ubuntu/.
3. **Open only the web ports:**
   ```bash
   sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
   ```
4. **Get the code and create `.env`:**
   ```bash
   git clone https://github.com/weinsg/bb-cafe-pos-2026-public.git beansbeats
   cd beansbeats
   cp .env.example .env
   ```
   Fill in `SECRET_KEY`, `POSTGRES_PASSWORD`, `ADMIN_PASSWORD` and `DOMAIN`. Without your own domain, use the server's IP with dashes plus `.sslip.io` (IP `1.2.3.4` → `1-2-3-4.sslip.io`).
5. **Start it:**
   ```bash
   docker compose -f docker-compose.prod.yml up --build -d
   docker compose -f docker-compose.prod.yml ps
   ```
   `app`, `db` and `caddy` should show **Up**. Open `https://<DOMAIN>`: the padlock shows HTTPS is working.
6. **Add the staff logins** (as in step 2.7, with `-f docker-compose.prod.yml` after `docker compose`).
7. **Set up backups** (section 6).

**To update later:**
```bash
cd beansbeats
git pull
docker compose -f docker-compose.prod.yml up --build -d
```

## 4. Set up the booth devices

1. Connect every device to the internet (hall Wi-Fi or a phone hotspot) and open the **HTTPS** link.
2. **Sign in once on each device while online.** Each device gets its own order letter (A, B…). Keep using the same browser on that device, and don't clear its data.
3. **Order station:** open **New order**.
4. **Queue station:** open **Queue** on the laptop. On the second screen, open `https://<DOMAIN>/board`, press **F11** for full screen, and leave it running. To mark an order ready by number, type its 3 digits on the number pad (A112 → `112`) and press Enter.
5. **Pickup station:** open **Pickup**, tap **Start camera** and allow the camera. **Scan from photo** is the backup if the live camera can't be used.
6. **Booth Lead / Admin:** in **Settings**, check the booth settings (Making step, Collect on scan, reminders, Cancel button, waiting-time estimate and how it reads) and, as Admin, **Settings → Ready board** (Ready side, charity counter and its position, with a live preview and one Save).
7. **Test before opening:** place one order and take it through to Collected. Check the guest page, the board and the pickup scan.

If the Wi-Fi drops, keep working: each device saves orders and taps and sends them when the connection returns. The badge on each device shows **Synced** or how many changes are waiting.

## 5. Reset between event days

At the end of each event day (or the next morning, before the first order):

1. Reconnect every staff device and check each one shows **Synced**.
2. Sign in as **Booth Lead** or **Admin** and open **Settings → Reset day**.
3. Choose the day to close (normally yesterday), then **Review day reset**.
4. Confirm the devices have synced, then **Close day and clear names**.

What happens: that day's orders leave the queue and the board, and **guests' first names are cleared**. Sales, the daily report and the audit trail stay, without guest names. The next day starts again from order 001. Export that day's CSV from **Daily sales → Export CSV ▾** first if you need it for the POS check (Sales by item, Cancelled orders with the refund tally, Discarded drinks or All orders).

## 6. Backups

`scripts/backup_db.sh` saves a compressed copy of the database to `~/backups` on the server and keeps 14 days. To run it every night at 03:15, run `crontab -e` on the server and add (change the folder if yours differs):

```
15 3 * * * bash /home/<user>/beansbeats/scripts/backup_db.sh >> /home/<user>/backups/backup.log 2>&1
```

**Copy backups to your computer:** `scp "<user>@<server>:backups/*.sql.gz" ./backups/`

**Restore** (this replaces the live data):
```bash
gunzip -c FILE.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U beansbeats -d beansbeats_db
```

## 7. Run the tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python tests/run_tests.py
```

The backend tests use a throw-away SQLite database and make their own random passwords. To run them on PostgreSQL, set `TEST_DATABASE_URL` to a PostgreSQL URL first. The same tests run on GitHub on every push.

Optional, on a test server only (their orders count in sales):
- `scripts/load_guest_pages.py`: opens many guest pages at once (300 pages passed).
- `scripts/simulate_booth.py`: a busy booth placing, making and collecting orders (set `BB_SIM_PASSWORD` to a staff login's password).

## 8. FAQ

### An order page says it cannot connect, or a screen looks out of date

This can happen on a phone or tablet that still has an old tab of the app open, for example after the app was updated or after an earlier test.

1. Close every tab of the site on that device.
2. Clear the browser data for the site:
   - **iPhone / iPad Safari:** Settings → Apps → Safari → Advanced → Website Data → find the site → Delete (or Settings → Apps → Safari → Clear History and Website Data).
   - **Chrome (Android, iPhone, laptop):** ⋮ → Delete browsing data → tick **Cookies and site data** and **Cached images and files** → Delete.
3. Open the page again. A guest scans the order's QR code again (staff can show it from the ticket's QR button in **Queue**).

**Booth devices:** before clearing data on a staff tablet or laptop, check the side bar shows **Synced**. Orders waiting to sync are kept in the browser, and clearing its data deletes them.

### Two orders end in the same number (e.g. A001 and T001)

Each tablet numbers its own orders, so two tablets can both have an order ending in 001. If staff type **001** + Enter on the Queue and both orders are still being made, nothing changes yet: the Queue lists them **oldest first**, with name and time ("1 · A001 Ann · 8:35 pm", "2 · T001 Ben · 8:37 pm"). Tap the right one, or type **1** or **2** + Enter on the number pad. An order that is already Ready is never listed, so when only one order with those digits is still being made it becomes Ready straight away. Typing the full number (A001) also works.

### Testing a new version

Before testing a new version, close the old tabs and clear the site's browsing data on every test device (steps above), so each device loads the new version instead of its saved copy.

## Folders

| Folder | Contents |
| --- | --- |
| `app/` | Server (FastAPI): orders, sync, live updates, settings, reports, staff commands |
| `frontend/` | Web app (React): staff screens, guest page, ready board |
| `tests/` | Backend tests |
| `scripts/` | Backups, guest-page load test, booth simulator |
| `docs/` | Technical summary (PDF) and submission memo |
