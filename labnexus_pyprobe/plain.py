"""Plain-text front end for logs, cron jobs and dumb terminals."""

from __future__ import annotations

import getpass
import logging
import sys

from .client import AuthError, LabNexusClient
from .formatting import human_size
from .watcher import ProbeEvent, Watcher

log = logging.getLogger("pyprobe")

LEVELS = {
    "error": logging.ERROR,
    "failed": logging.WARNING,
    "scan": logging.DEBUG,
    "skipped": logging.INFO,
}


def setup_logging(verbosity: int = 0, quiet: bool = False, log_file: str | None = None) -> None:
    """Configure the ``pyprobe`` logger for the plain front end."""
    level = logging.WARNING if quiet else (logging.DEBUG if verbosity else logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def prompt_login(client: LabNexusClient, email: str | None = None) -> str:
    """Credential prompt without any terminal styling."""
    while True:
        user = email or input("LabNexus email: ").strip()
        password = getpass.getpass("Password: ")
        try:
            client.login(user, password)
        except AuthError as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            email = None
            continue
        print(f"Session opened for {user}.")
        return user


def report(event: ProbeEvent) -> None:
    """Log one watcher event."""
    suffix = f" ({human_size(event.size)})" if event.size is not None else ""
    detail = f" - {event.detail}" if event.detail else ""
    log.log(LEVELS.get(event.kind, logging.INFO), "%s%s%s", event.message, suffix, detail)


def run(watcher: Watcher) -> None:
    """Block until the watcher stops or the user interrupts."""
    try:
        watcher.run()
    except KeyboardInterrupt:
        watcher.stop()
    stats = watcher.stats
    log.info(
        "Session closed: %d uploaded, %d failed, %s sent.",
        stats.uploaded,
        stats.failed,
        human_size(stats.bytes_sent),
    )
