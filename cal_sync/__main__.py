"""
cal_sync — main entry point.

Usage:
    python -m cal_sync                        # runs as a daemon (uses config/config.toml)
    python -m cal_sync --config /path/to.toml # custom config
    python -m cal_sync --once                 # sync once and exit
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import config as cfg_module
from . import google_cal, outlook_cal, writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("cal-sync")


def _sync(cfg: cfg_module.Config) -> None:
    chunks: list[bytes] = []

    try:
        log.info("Fetching Google Calendar …")
        chunks.append(google_cal.fetch(cfg.google))
    except Exception as exc:
        log.error("Google Calendar fetch failed: %s", exc)

    try:
        log.info("Fetching Outlook Calendar …")
        chunks.append(outlook_cal.fetch(cfg.outlook))
    except Exception as exc:
        log.error("Outlook Calendar fetch failed: %s", exc)

    try:
        writer.merge_and_write(chunks, cfg.sync.output_path)
    except Exception as exc:
        log.error("Failed to write ICS: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cal-sync",
        description="Sync Google + Outlook calendars into a single ICS file.",
    )
    parser.add_argument(
        "--config",
        default="config/config.toml",
        metavar="PATH",
        help="Path to TOML config file (default: config/config.toml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync then exit instead of running as a daemon",
    )
    args = parser.parse_args()

    cfg = cfg_module.load(args.config)

    if args.once:
        _sync(cfg)
        return

    # Graceful shutdown on SIGTERM / SIGINT
    _running = True

    def _stop(sig, frame):
        nonlocal _running
        log.info("Received signal %s — shutting down", signal.Signals(sig).name)
        _running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    interval = cfg.sync.interval_minutes * 60
    log.info(
        "cal-sync daemon started — syncing every %d min → %s",
        cfg.sync.interval_minutes,
        cfg.sync.output_path,
    )

    while _running:
        _sync(cfg)
        # Sleep in 1-second ticks so SIGTERM is handled promptly
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)

    log.info("cal-sync stopped")


if __name__ == "__main__":
    main()
