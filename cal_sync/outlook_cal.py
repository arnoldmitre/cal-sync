"""
cal_sync.outlook — fetches events from Microsoft/Outlook via Microsoft Graph API.

Auth flow: OAuth2 device-code flow (works on headless servers too).
Token is cached to config.token_file via MSAL's file-based token cache.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import msal
import requests
from icalendar import Calendar, Event

from .config import OutlookConfig

log = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["Calendars.Read", "offline_access"]


def _build_app(cfg: OutlookConfig) -> msal.PublicClientApplication:
    cache = msal.SerializableTokenCache()
    if cfg.token_file.exists():
        cache.deserialize(cfg.token_file.read_text())

    app = msal.PublicClientApplication(
        client_id=cfg.client_id,
        authority=f"https://login.microsoftonline.com/{cfg.tenant_id}",
        token_cache=cache,
    )
    return app, cache


def _persist_cache(cache: msal.SerializableTokenCache, token_file: Path) -> None:
    if cache.has_state_changed:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(cache.serialize())


def _acquire_token(cfg: OutlookConfig) -> str:
    app, cache = _build_app(cfg)
    accounts = app.get_accounts()

    result = None
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow.get('error_description')}")
        print(flow["message"], flush=True)
        result = app.acquire_token_by_device_flow(flow)

    _persist_cache(cache, cfg.token_file)

    if "access_token" not in result:
        raise RuntimeError(
            f"Could not acquire Outlook token: {result.get('error_description')}"
        )
    return result["access_token"]


def _graph_get(token: str, url: str, params: dict | None = None) -> dict:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _calendar_ids(token: str, requested: list[str]) -> list[str]:
    if requested:
        return requested
    data = _graph_get(token, f"{_GRAPH_BASE}/me/calendars")
    return [c["id"] for c in data.get("value", [])]


def fetch(cfg: OutlookConfig) -> bytes:
    """Return a merged ICS bytes object for all requested Outlook calendars."""
    if not cfg.client_id:
        log.warning("Outlook client_id not configured — skipping Outlook sync")
        return b""

    token = _acquire_token(cfg)

    cal = Calendar()
    cal.add("prodid", "-//cal-sync//outlook//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for cal_id in _calendar_ids(token, cfg.calendar_ids):
        log.info("Outlook: fetching calendar %s", cal_id)
        url = f"{_GRAPH_BASE}/me/calendars/{cal_id}/events"
        params = {
            "$select": "id,subject,start,end,body,location,lastModifiedDateTime,showAs",
            "$filter": f"start/dateTime ge '{now}'",
            "$orderby": "start/dateTime",
            "$top": 999,
        }
        while url:
            data = _graph_get(token, url, params)
            for item in data.get("value", []):
                _add_event(cal, item)
            url = data.get("@odata.nextLink")
            params = None  # nextLink already contains query params

    log.info("Outlook: done")
    return cal.to_ical()


def _parse_dt(dt_obj: dict) -> datetime | None:
    value = dt_obj.get("dateTime")
    tz = dt_obj.get("timeZone", "UTC")
    if not value:
        return None
    try:
        import zoneinfo

        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(tz))
        return dt
    except Exception:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _add_event(cal: Calendar, item: dict) -> None:
    ev = Event()
    ev.add("uid", item.get("id", "") + "@outlook.cal-sync")
    ev.add("summary", item.get("subject", "(no title)"))

    start_dt = _parse_dt(item.get("start", {}))
    end_dt = _parse_dt(item.get("end", {}))
    if start_dt:
        ev.add("dtstart", start_dt)
    if end_dt:
        ev.add("dtend", end_dt)

    body = item.get("body", {}).get("content", "")
    if body:
        # Strip HTML tags for plain-text description
        import re
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body:
            ev.add("description", body)

    loc = item.get("location", {}).get("displayName", "")
    if loc:
        ev.add("location", loc)

    modified = item.get("lastModifiedDateTime")
    if modified:
        ev.add("last-modified", datetime.fromisoformat(modified.replace("Z", "+00:00")))

    status_map = {
        "busy": "CONFIRMED",
        "free": "TENTATIVE",
        "tentative": "TENTATIVE",
        "oof": "CONFIRMED",
        "workingElsewhere": "CONFIRMED",
    }
    show_as = item.get("showAs", "busy")
    ev.add("status", status_map.get(show_as, "CONFIRMED"))

    cal.add_component(ev)
