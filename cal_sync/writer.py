"""
cal_sync.writer — merges multiple ICS byte-streams and writes the result
atomically to the configured output path (e.g. an SMB mount).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from icalendar import Calendar

log = logging.getLogger(__name__)


def merge_and_write(ics_chunks: list[bytes], output_path: Path) -> None:
    """Merge ICS chunks and atomically write the combined file."""
    merged = Calendar()
    merged.add("prodid", "-//cal-sync//merged//EN")
    merged.add("version", "2.0")
    merged.add("calscale", "GREGORIAN")

    seen_uids: set[str] = set()
    event_count = 0

    for chunk in ics_chunks:
        if not chunk:
            continue
        try:
            source_cal = Calendar.from_ical(chunk)
        except Exception as exc:
            log.warning("Could not parse ICS chunk: %s", exc)
            continue

        for component in source_cal.walk():
            if component.name != "VEVENT":
                continue
            uid = str(component.get("uid", ""))
            if uid in seen_uids:
                log.debug("Deduplicating event uid=%s", uid)
                continue
            seen_uids.add(uid)
            merged.add_component(component)
            event_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_path, merged.to_ical())
    log.info("Written %d events → %s", event_count, output_path)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write data to a temp file in the same directory, then rename (atomic on POSIX)."""
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".cal-sync-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
