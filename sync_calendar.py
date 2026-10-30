#!/usr/bin/env python3
"""
Exchange to Google Calendar One-Way Replicator
----------------------------------------------
- Source: Microsoft Exchange calendar inside macOS Calendar app (STRICTLY READ-ONLY).
- Destination: Google Calendar (via Google Calendar REST API).
- Exchange is the single source of truth:
    * The script NEVER modifies, edits, or deletes anything in Exchange.
    * If an event is deleted on Google Calendar, nothing happens to Exchange.
    * On subsequent syncs, missing events are re-created in Google Calendar.
    * If an event is deleted from Exchange, it is removed from Google Calendar.
"""

import os
import sys
import re
import json
import logging
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from dateutil import parser as date_parser

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CalendarSync")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def load_config(config_path=CONFIG_FILE):
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_jxa(script_code):
    """Executes a JavaScript for Automation (JXA) snippet via osascript."""
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script_code],
            capture_output=True,
            text=True,
            check=True
        )
        return proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() or e.stdout.strip()
        logger.error(f"AppleScript/JXA execution error: {err}")
        return None


def ensure_calendar_running():
    """Ensures macOS Calendar.app is running in the background."""
    try:
        subprocess.run(["open", "-g", "-a", "Calendar"], check=False)
    except Exception as e:
        logger.debug(f"Could not open Calendar via 'open': {e}")


HELPER_BIN = os.path.join(SCRIPT_DIR, "fetch_events")


def list_apple_calendars():
    """Lists all calendars in macOS Calendar app using native EventKit."""
    if os.path.exists(HELPER_BIN):
        try:
            proc = subprocess.run(
                [HELPER_BIN, "--list-calendars"],
                capture_output=True,
                text=True,
                check=True
            )
            raw = proc.stdout.strip()
            data = json.loads(raw)
            return data
        except subprocess.CalledProcessError as e:
            err = e.stderr.strip()
            if "-1743" in err or "Not authorized" in err:
                logger.error("\n" + "=" * 60)
                logger.error("macOS PRIVACY PERMISSION REQUIRED:")
                logger.error("Terminal was blocked from accessing your Calendar.")
                logger.error("Please grant permission in System Settings -> Privacy & Security -> Calendars.")
                logger.error("=" * 60 + "\n")
            else:
                logger.error(f"Error reading calendars via EventKit: {err}")
        except Exception as e:
            logger.error(f"Failed to parse calendar list: {e}")

    return []


def get_exchange_events(cal_name, cal_id, start_dt, end_dt):
    """
    STRICTLY READ-ONLY: Extracts events from macOS Calendar (Exchange) via native EventKit.
    Never modifies, writes, or deletes anything in Apple Calendar or Exchange.
    """
    if os.path.exists(HELPER_BIN):
        target_id = cal_id
        if not target_id:
            all_cals = list_apple_calendars()
            for c in all_cals:
                c_name = c.get("name", "")
                if c_name.lower() == (cal_name or "").lower():
                    # Prefer Exchange calendar if multiple exist
                    if c.get("isExchange", False) or not target_id:
                        target_id = c.get("id")
                        if c.get("isExchange", False):
                            break

        if target_id:
            now_utc = datetime.now(timezone.utc)
            s_dt = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
            e_dt = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=timezone.utc)
            days_past = max(1, (now_utc - s_dt).days + 1)
            days_future = max(1, (e_dt - now_utc).days + 1)

            try:
                proc = subprocess.run(
                    [
                        HELPER_BIN, "--get-events",
                        "--calendar-id", target_id,
                        "--days-past", str(days_past),
                        "--days-future", str(days_future)
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )
                events = json.loads(proc.stdout.strip())
                return events
            except Exception as e:
                logger.error(f"EventKit error fetching events: {e}")

    return []


def get_google_service():
    """Initializes and authenticates Google Calendar API service."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.warning(f"Could not load token.json: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google OAuth token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}. Re-authenticating...")
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                logger.error(
                    f"\nGoogle credentials file not found: {CREDENTIALS_FILE}\n"
                    "Please download your credentials.json from Google Cloud Console and place it in the calendar-sync directory.\n"
                    "See README.md for instructions."
                )
                sys.exit(1)

            logger.info("Starting Google OAuth authorization in your browser...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token_out:
            token_out.write(creds.to_json())
            logger.info(f"Saved OAuth token to {TOKEN_FILE}")

    return build("calendar", "v3", credentials=creds)


def get_or_create_google_calendar(service, cal_name):
    """Finds or creates the designated destination calendar in Google Calendar."""
    if cal_name.strip().lower() == "primary":
        return "primary"

    page_token = None
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for item in calendar_list.get("items", []):
            if item.get("summary", "").strip().lower() == cal_name.strip().lower():
                logger.info(f"Found destination Google Calendar: '{item.get('summary')}' (ID: {item.get('id')})")
                return item["id"]
        page_token = calendar_list.get("nextPageToken")
        if not page_token:
            break

    # Not found, create it
    logger.info(f"Creating new dedicated Google Calendar: '{cal_name}'...")
    body = {
        "summary": cal_name,
        "description": "Replicated automatically from Apple Calendar / Exchange (Read-Only Mirror)",
        "timeZone": "UTC"
    }
    created = service.calendars().insert(body=body).execute()
    logger.info(f"Created Google Calendar '{cal_name}' (ID: {created.get('id')})")
    return created["id"]


def get_google_events_index(service, cal_id, start_dt, end_dt):
    """
    Fetches events from the destination Google Calendar and indexes them by exchange_uid.
    """
    index = {}
    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()

    page_token = None
    while True:
        res = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            pageToken=page_token
        ).execute()

        for ev in res.get("items", []):
            ext = ev.get("extendedProperties", {}).get("private", {})
            ex_uid = ext.get("exchange_uid")
            if ex_uid:
                index[ex_uid] = ev

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    return index


def sanitize_event(ex_ev, config):
    """
    Applies Hybrid Sanitization:
    - Masks internal order / ticket identifiers in title (e.g. SO 0073980841 -> [Order])
    - Cleans Location to 'Microsoft Teams' or physical city/place, stripping tracking links and passcodes
    - Cleans Description:
        * Strips internal enterprise domains (*.corp, *.int.sap, *.hec.net.sap)
        * Strips meeting passcodes, PINs, and dial-in IDs
        * Strips boilerplate disclaimers (Global Mobility, Legal notices)
        * Truncates / preserves clean high-level agenda
    """
    san_cfg = config.get("sanitization", {})
    if not san_cfg.get("enabled", True):
        return ex_ev

    mode = san_cfg.get("mode", "hybrid")
    if mode == "off":
        return ex_ev

    sanitized = dict(ex_ev)
    summary = sanitized.get("summary", "")
    description = sanitized.get("description", "")
    location = sanitized.get("location", "")

    # Mode: Busy Only
    if mode == "busy_only":
        sanitized["summary"] = san_cfg.get("busy_replacement_title", "Busy")
        sanitized["description"] = ""
        sanitized["location"] = ""
        return sanitized

    # Mode: Title Only
    if mode == "title_only":
        sanitized["description"] = ""
        if san_cfg.get("clean_location", True):
            loc_lower = location.lower()
            if "teams.microsoft.com" in loc_lower or "teams" in loc_lower:
                sanitized["location"] = "Microsoft Teams"
            elif "zoom.us" in loc_lower:
                sanitized["location"] = "Zoom"
            elif "webex" in loc_lower:
                sanitized["location"] = "Webex"
            elif loc_lower.startswith("http://") or loc_lower.startswith("https://"):
                sanitized["location"] = "Virtual Meeting"
        return sanitized

    # Mode: Hybrid (1 + 3)
    # 1. Clean Title: mask internal codes like 'SO 0073980841' -> '[Order]'
    if san_cfg.get("mask_order_numbers", True):
        summary = re.sub(r'\b(SO\s*\d{6,12})\b', '[Order]', summary, flags=re.IGNORECASE)
        summary = re.sub(r'\b(CRM:\s*\d+)\b', '[CRM]', summary, flags=re.IGNORECASE)
    sanitized["summary"] = summary

    # 2. Clean Location
    if san_cfg.get("clean_location", True):
        loc_lower = location.lower()
        if "teams.microsoft.com" in loc_lower or "teams" in loc_lower:
            sanitized["location"] = "Microsoft Teams"
        elif "zoom.us" in loc_lower:
            sanitized["location"] = "Zoom"
        elif "webex" in loc_lower:
            sanitized["location"] = "Webex"
        elif "meet.google.com" in loc_lower:
            sanitized["location"] = "Google Meet"
        elif loc_lower.startswith("http://") or loc_lower.startswith("https://"):
            sanitized["location"] = "Virtual Meeting"

    # 3. Clean Description
    if san_cfg.get("strip_boilerplate", True):
        boilerplate_patterns = [
            r"Do not decline this invitation![\s\S]*?(?=Kind Regards|$)",
            r"If you’re staffed to deliver the service onsite[\s\S]*?(?=Do not decline|Kind Regards|$)",
            r"Attention Service Team Leads and TQMs:[\s\S]*?(?=Kind Regards|$)",
            r"Maintaining regular health checks[\s\S]*?(?=NOTES|Web Conference|$)",
            r"INTERNAL[\s\S]*?INTERNAL",
            r"Participant Passcode:[\s\S]*?\n",
            r"Phone conference ID:[\s\S]*?\n",
            r"Passcode:\s*[A-Za-z0-9#*]+\b",
            r"Meeting ID:\s*[\d\s]+",
            r"Reset dial-in PIN<[^>]*>"
        ]
        for pat in boilerplate_patterns:
            description = re.sub(pat, "", description, flags=re.IGNORECASE)

    if san_cfg.get("strip_internal_urls", True):
        description = re.sub(r'https?://[^\s<>"]*(?:\.corp|\.int\.sap|\.hec\.net\.sap|launchpad\.one)[^\s<>"]*', '[internal link removed]', description, flags=re.IGNORECASE)

    if san_cfg.get("strip_passcodes", True):
        description = re.sub(r'(?i)\bpasscode[:\s]+[^\s]+', 'Passcode: [removed]', description)
        description = re.sub(r'(?i)\bconference ID[:\s]+[^\s]+', 'ID: [removed]', description)
        description = re.sub(r'(?i)\bPIN[:\s]+\d+', 'PIN: [removed]', description)

    description = re.sub(r'\n{3,}', '\n\n', description).strip()

    max_len = san_cfg.get("max_description_length", 500)
    if max_len and len(description) > max_len:
        description = description[:max_len].rsplit(' ', 1)[0] + " ... [trimmed]"

    sanitized["description"] = description
    return sanitized


def format_google_event_payload(ex_ev, config=None):
    """Converts an Apple Calendar / Exchange event into Google Calendar API format with sanitization and multi-day cropping."""
    if config:
        ex_ev = sanitize_event(ex_ev, config)

    uid = ex_ev["uid"]
    summary = ex_ev.get("summary") or "(No Title)"
    description = ex_ev.get("description") or ""
    location = ex_ev.get("location") or ""
    all_day = ex_ev.get("allDay", False)

    start_parsed = date_parser.isoparse(ex_ev["startDate"])
    end_parsed = date_parser.isoparse(ex_ev["endDate"])

    # Multi-day event handling / cropping
    if config:
        multi_cfg = config.get("multi_day_handling", {})
        if multi_cfg.get("enabled", True):
            span_seconds = (end_parsed - start_parsed).total_seconds()
            span_days = span_seconds / 86400.0

            # Skip crazy multi-year or ultra-long events (e.g. 700-day recurring banners)
            max_days = multi_cfg.get("skip_if_longer_than_days", 30)
            if max_days and span_days > max_days:
                return None

            threshold = multi_cfg.get("threshold_days", 1.0)
            if span_days > threshold:
                days_count = max(1, round(span_days))
                summary = f"[{days_count}d] {summary}"
                if all_day:
                    # Crop to single-day banner
                    end_parsed = start_parsed + timedelta(days=1)
                else:
                    # Crop timed event to finish on Day 1
                    target_end = start_parsed.replace(
                        hour=end_parsed.hour,
                        minute=end_parsed.minute,
                        second=end_parsed.second
                    )
                    if target_end <= start_parsed:
                        target_end = min(start_parsed + timedelta(hours=4), start_parsed.replace(hour=23, minute=59, second=59))
                    end_parsed = target_end

    body = {
        "summary": summary,
        "location": location,
        "description": description,
        "extendedProperties": {
            "private": {
                "exchange_uid": uid,
                "sync_source": "exchange_macos"
            }
        }
    }

    if all_day:
        s_date = start_parsed.strftime("%Y-%m-%d")
        if end_parsed <= start_parsed:
            e_date = (start_parsed + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            e_date = end_parsed.strftime("%Y-%m-%d")
        body["start"] = {"date": s_date}
        body["end"] = {"date": e_date}
    else:
        body["start"] = {"dateTime": start_parsed.isoformat()}
        body["end"] = {"dateTime": end_parsed.isoformat()}

    recurrence = ex_ev.get("recurrence")
    if recurrence:
        if not recurrence.upper().startswith("RRULE:"):
            recurrence = "RRULE:" + recurrence
        body["recurrence"] = [recurrence]

    return body


def sync(config, dry_run=False):
    """
    Main sync loop.
    Source: Apple Calendar (Exchange) -> Destination: Google Calendar
    STRICTLY ONE-WAY.
    """
    src_name = config.get("source_calendar_name", "Calendar")
    src_id = config.get("source_calendar_id", "")
    dest_name = config.get("destination_calendar_name", "Exchange (Work)")
    days_past = int(config.get("sync_days_past", 14))
    days_future = int(config.get("sync_days_future", 90))
    delete_removed = bool(config.get("delete_from_google_if_removed_from_exchange", True))

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=days_past)
    end_dt = now + timedelta(days=days_future)

    logger.info(f"Sync window: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")
    logger.info(f"Target Exchange calendar: '{src_name}' (ID: '{src_id or 'auto'}')")
    logger.info(f"Target Google calendar: '{dest_name}'")
    san_mode = config.get("sanitization", {}).get("mode", "hybrid") if config.get("sanitization", {}).get("enabled", True) else "disabled"
    logger.info(f"Sanitization mode: {san_mode}")

    # 1. Read events from Exchange via Apple Calendar (READ-ONLY)
    logger.info("Reading events from Apple Calendar (Exchange)...")
    exchange_events = get_exchange_events(src_name, src_id, start_dt, end_dt)
    logger.info(f"Retrieved {len(exchange_events)} events from Exchange.")

    # 2. Connect to Google Calendar
    service = get_google_service()
    g_cal_id = get_or_create_google_calendar(service, dest_name)

    # 3. Read existing synced events from Google Calendar
    logger.info("Fetching existing mirrored events from Google Calendar...")
    google_events_index = get_google_events_index(service, g_cal_id, start_dt, end_dt)
    logger.info(f"Found {len(google_events_index)} mirrored events in Google Calendar.")

    created_count = 0
    updated_count = 0
    deleted_count = 0

    exchange_uid_set = set()

    # 4. Replicate from Exchange -> Google
    for ex_ev in exchange_events:
        uid = ex_ev["uid"]

        payload = format_google_event_payload(ex_ev, config)
        if payload is None:
            # Event was skipped (e.g. exceeded maximum multi-day duration like 700-day banners)
            continue

        exchange_uid_set.add(uid)
        summary = payload.get("summary", "(No Title)")

        if uid not in google_events_index:
            logger.info(f"[CREATE] Adding to Google: '{summary}' ({ex_ev.get('startDate')})")
            if not dry_run:
                try:
                    service.events().insert(calendarId=g_cal_id, body=payload).execute()
                    created_count += 1
                except Exception as e:
                    logger.error(f"Failed to create event '{summary}': {e}")
            else:
                created_count += 1
        else:
            g_ev = google_events_index[uid]
            g_summary = g_ev.get("summary", "")
            g_desc = g_ev.get("description", "")
            g_loc = g_ev.get("location", "")

            needs_update = False
            if g_summary != payload["summary"] or g_desc != payload["description"] or g_loc != payload["location"]:
                needs_update = True

            # Check start times
            if payload.get("start", {}).get("date"):
                if g_ev.get("start", {}).get("date") != payload["start"]["date"]:
                    needs_update = True
            elif payload.get("start", {}).get("dateTime"):
                g_start_dt = date_parser.isoparse(g_ev.get("start", {}).get("dateTime"))
                p_start_dt = date_parser.isoparse(payload["start"]["dateTime"])
                if abs((g_start_dt - p_start_dt).total_seconds()) > 5:
                    needs_update = True

            # Check end times (crucial for cropped multi-day events)
            if payload.get("end", {}).get("date"):
                if g_ev.get("end", {}).get("date") != payload["end"]["date"]:
                    needs_update = True
            elif payload.get("end", {}).get("dateTime"):
                if g_ev.get("end", {}).get("dateTime"):
                    g_end_dt = date_parser.isoparse(g_ev.get("end", {}).get("dateTime"))
                    p_end_dt = date_parser.isoparse(payload["end"]["dateTime"])
                    if abs((g_end_dt - p_end_dt).total_seconds()) > 5:
                        needs_update = True
                else:
                    needs_update = True

            if needs_update:
                logger.info(f"[UPDATE] Updating in Google: '{summary}'")
                if not dry_run:
                    try:
                        service.events().patch(calendarId=g_cal_id, eventId=g_ev["id"], body=payload).execute()
                        updated_count += 1
                    except Exception as e:
                        logger.error(f"Failed to update event '{summary}': {e}")
                else:
                    updated_count += 1

    # 5. Handle events removed from Exchange
    if delete_removed:
        if not exchange_events and google_events_index:
            logger.warning("[SAFETY GUARD] 0 events retrieved from Exchange while Google Calendar has existing mirrored events. Skipping deletions to prevent accidental wipe.")
        else:
            for ex_uid, g_ev in google_events_index.items():
                if ex_uid not in exchange_uid_set:
                    g_title = g_ev.get("summary", "(Untitled)")
                    logger.info(f"[DELETE] Event removed from Exchange, deleting from Google: '{g_title}'")
                    if not dry_run:
                        try:
                            service.events().delete(calendarId=g_cal_id, eventId=g_ev["id"]).execute()
                            deleted_count += 1
                        except Exception as e:
                            logger.error(f"Failed to delete event '{g_title}': {e}")
                    else:
                        deleted_count += 1

    logger.info("=" * 50)
    logger.info(f"Sync complete{' [DRY RUN]' if dry_run else ''}:")
    logger.info(f"  Created: {created_count}")
    logger.info(f"  Updated: {updated_count}")
    logger.info(f"  Deleted: {deleted_count}")
    logger.info("Source Exchange calendar remained 100% untouched.")
    logger.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Sync Apple Calendar (Exchange) to Google Calendar (Read-Only Source)")
    parser.add_argument("--sync", action="store_true", help="Perform synchronization")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying Google Calendar")
    parser.add_argument("--list-calendars", action="store_true", help="List all available calendars in macOS Calendar app")
    parser.add_argument("--config", default=CONFIG_FILE, help="Path to config.json")
    args = parser.parse_args()

    if args.list_calendars:
        logger.info("Fetching calendars from macOS Calendar app...")
        cals = list_apple_calendars()
        if not cals:
            print("\nNo calendars returned. Make sure Calendar.app is open and permissions are granted.")
            return
        print("\nAvailable Apple Calendars:")
        print("-" * 60)
        for c in cals:
            print(f"• Name: \"{c['name']}\"")
            print(f"  ID:   {c['id']}")
            if c.get("description"):
                print(f"  Desc: {c['description']}")
            print("-" * 60)
        return

    config = load_config(args.config)

    if args.sync or args.dry_run:
        sync(config, dry_run=args.dry_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
