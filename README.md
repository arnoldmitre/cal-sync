# Exchange to Google Calendar Replicator (macOS)

A robust, strictly one-way synchronization tool that replicates events from an **Exchange calendar** in the macOS Apple Calendar app to **Google Calendar (Gmail)**.

---

## Key Features

- **Strictly Read-Only on Source (Exchange)**:
  - The script **never** writes, modifies, or deletes anything in your Exchange account.
  - Exchange is treated as the single source of truth.
- **Native EventKit Engine**:
  - Uses a lightweight native macOS EventKit bridge (`fetch_events.m`) compiled with `clang`.
  - Runs in milliseconds directly against the macOS local calendar cache.
  - Does **not** require Calendar.app to stay open.
- **Smart Hybrid Sanitization (Data Privacy)**:
  - Preserves meeting titles while masking corporate order numbers (e.g. `SO 12345678` -> `[Order]`).
  - Converts verbose Teams/Zoom join URLs into clean labels (`Microsoft Teams`).
  - Automatically strips internal enterprise URLs (`*.corp`, `*.int.sap`, etc.), meeting passcodes, PINs, and automated HR/legal boilerplates.
- **Multi-Day Assignment Cropping**:
  - Automatically crops multi-day assignments (e.g. 4-day or 11-day continuous blocks) to a clean milestone on **Day 1** tagged with `[4d]`, keeping days 2, 3, and 4 completely clear.
  - Automatically filters out improperly configured multi-year banners (e.g. 700-day recurring blocks).
- **Safe Destination Handling**:
  - If you delete or modify an event in Google Calendar, Exchange is never touched.
  - On the next sync cycle, any missing events are re-created in Google Calendar.
  - If an event is removed or cancelled from Exchange, it will be cleaned up in Google Calendar automatically.
- **Dedicated Google Calendar**:
  - By default, events are placed into a separate Google Calendar called `"Exchange (Work)"` so your work schedule doesn't clutter your personal primary calendar (you can toggle it on/off in Google Calendar anytime).

---

## Quick Start Guide

### Step 1: Download Google API Credentials (One-time, 2 minutes)

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Calendar Sync`).
3. In the search bar at the top, search for **Google Calendar API** and click **Enable**.
4. Go to **APIs & Services** $\rightarrow$ **OAuth consent screen**:
   - Choose **External** and click **Create**.
   - Enter an app name (e.g. `Calendar Sync`) and your email address.
   - Under **Test users**, add your own Gmail address.
5. Go to **APIs & Services** $\rightarrow$ **Credentials**:
   - Click **Create Credentials** $\rightarrow$ **OAuth client ID**.
   - Select **Desktop App** as the Application Type.
   - Click **Create**.
6. Download the JSON credential file, rename it to **`credentials.json`**, and save it in:
   ```
   ~/calendar-sync/credentials.json
   ```

---

### Step 2: Run Setup

Open Terminal on your Mac and run:
```bash
cd ~/calendar-sync
./setup.sh
```
This automatically sets up the Python virtual environment, installs packages, compiles the native EventKit bridge, and initializes `config.json`.

---

### Step 3: Verify & Configure Calendar Names

1. See all calendars detected by EventKit:
   ```bash
   ./venv/bin/python3 sync_calendar.py --list-calendars
   ```

2. Open `config.json` to verify the settings:
   ```json
   {
     "source_calendar_name": "Calendar",
     "source_calendar_id": "",
     "destination_calendar_name": "Exchange (Work)",
     "sync_days_past": 14,
     "sync_days_future": 90,
     "delete_from_google_if_removed_from_exchange": true,
     "resync_if_deleted_on_destination": true,
     "sanitization": {
       "enabled": true,
       "mode": "hybrid",
       "mask_order_numbers": true,
       "clean_location": true,
       "strip_internal_urls": true,
       "strip_passcodes": true,
       "strip_boilerplate": true,
       "max_description_length": 500
     },
     "multi_day_handling": {
       "enabled": true,
       "action": "crop_to_first_day",
       "threshold_days": 1.0,
       "skip_if_longer_than_days": 30
     }
   }
   ```

---

### Step 4: Test & Run

1. **Dry Run (Preview changes without touching Google Calendar)**:
   ```bash
   ./venv/bin/python3 sync_calendar.py --dry-run
   ```
   *On your first run, a browser window will open asking you to sign in with your Google account. After approving, a `token.json` file is saved locally so you won't need to sign in again.*

2. **Full Live Sync**:
   ```bash
   ./venv/bin/python3 sync_calendar.py --sync
   ```

---

### Step 5: Background Auto-Sync via launchd

To have macOS sync silently in the background every 30 minutes without opening terminal or Calendar.app:

```bash
cp com.user.calendarsync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.calendarsync.plist
```

To monitor background logs:
```bash
tail -f sync.log
```
