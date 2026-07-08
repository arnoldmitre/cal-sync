"""
cal_sync — configuration loader
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class SyncConfig:
    interval_minutes: int = 15
    output_path: Path = Path("/mnt/smb/calendars/all.ics")


@dataclass
class GoogleConfig:
    credentials_file: Path = Path("config/google_credentials.json")
    token_file: Path = Path("config/google_token.json")
    calendar_ids: list[str] = field(default_factory=lambda: ["primary"])


@dataclass
class OutlookConfig:
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = "common"
    token_file: Path = Path("config/outlook_token.json")
    calendar_ids: list[str] = field(default_factory=list)


@dataclass
class Config:
    sync: SyncConfig = field(default_factory=SyncConfig)
    google: GoogleConfig = field(default_factory=GoogleConfig)
    outlook: OutlookConfig = field(default_factory=OutlookConfig)


def load(path: str | Path = "config/config.toml") -> Config:
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(
            f"[cal-sync] config not found at {cfg_path!r}. "
            "Copy config/config.example.toml → config/config.toml and fill in your credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    sync_raw = raw.get("sync", {})
    google_raw = raw.get("google", {})
    outlook_raw = raw.get("outlook", {})

    return Config(
        sync=SyncConfig(
            interval_minutes=sync_raw.get("interval_minutes", 15),
            output_path=Path(sync_raw.get("output_path", "/mnt/smb/calendars/all.ics")),
        ),
        google=GoogleConfig(
            credentials_file=Path(google_raw.get("credentials_file", "config/google_credentials.json")),
            token_file=Path(google_raw.get("token_file", "config/google_token.json")),
            calendar_ids=google_raw.get("calendar_ids", ["primary"]),
        ),
        outlook=OutlookConfig(
            client_id=outlook_raw.get("client_id", ""),
            client_secret=outlook_raw.get("client_secret", ""),
            tenant_id=outlook_raw.get("tenant_id", "common"),
            token_file=Path(outlook_raw.get("token_file", "config/outlook_token.json")),
            calendar_ids=outlook_raw.get("calendar_ids", []),
        ),
    )
