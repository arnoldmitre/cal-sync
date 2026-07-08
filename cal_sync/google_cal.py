"""
cal_sync.google — fetches events from Google Calendar and returns raw ICS data.

Auth flow: OAuth2 with offline access. On first run the user is prompted in a
browser. The token is cached to config.token_file for subsequent runs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from icalendar import Calendar, Event, vDatetime

from .config import GoogleConfig

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _get_credentials(cfg: GoogleConfig) -> Credentials:
    creds: Credentials | None = None

    if cfg.token_file.exists():
        creds = Credentials.from_authorized_user_file(str(cfg.token_file), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(cfg.credentials_file), _SCOPES
            )
            creds = flow.run_local_server(port=0)
        cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.token_file.write_text(creds.to_json())

    return creds


def _calendar_ids(service, requested: list[str]) -> list[str]:
    if requested:
        return requested
    items = service.calendarList().list().execute().get("items", [])
    return [c["id"] for c in items]


def fetch(cfg: GoogleConfig) -> bytes:
    """Return a merged ICS bytes object for all requested Google calendars."""
    creds = _get_credentials(cfg)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    cal = Calendar()
    cal.add("prodid", "-//cal-sync//google//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    now = datetime.now(timezone.utc).isoformat()

    for cal_id in _calendar_ids(service, cfg.calendar_ids):
        log.info("Google: fetching calendar %s", cal_id)
        page_token = None
        while True:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=now,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in result.get("items", []):
                _add_event(cal, item)
            page_token = result.get("nextPageToken")
            if not page_token:
                break

    log.info("Google: done")
    return cal.to_ical()


def _parse_dt(value: str) -> datetime | None:
    """Parse an ISO-8601 datetime or date string from Google API."""
    from datetime import date as date_type

    if not value:
        return None
    try:
        # Full datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Date-only (all-day event)
        return datetime.fromisoformat(value + "T00:00:00+00:00")


def _add_event(cal: Calendar, item: dict) -> None:
    ev = Event()
    ev.add("uid", item.get("id", "") + "@google.cal-sync")
    ev.add("summary", item.get("summary", "(no title)"))

    start = item.get("start", {})
    end = item.get("end", {})
    start_dt = _parse_dt(start.get("dateTime") or start.get("date", ""))
    end_dt = _parse_dt(end.get("dateTime") or end.get("date", ""))

    if start_dt:
        ev.add("dtstart", start_dt)
    if end_dt:
        ev.add("dtend", end_dt)

    if item.get("description"):
        ev.add("description", item["description"])
    if item.get("location"):
        ev.add("location", item["location"])
    if item.get("status"):
        ev.add("status", item["status"].upper())

    updated = item.get("updated")
    if updated:
        ev.add("last-modified", _parse_dt(updated))

    cal.add_component(ev)
