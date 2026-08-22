"""Backwards-compatible wrapper around the modern watcher.

Earlier releases exposed a single ``FileWatcher(dir_path, server_url)`` function
and a ``main()`` here. Both still work; new code should use
:class:`labnexus_pyprobe.watcher.Watcher` and :mod:`labnexus_pyprobe.cli`.
"""

from __future__ import annotations

from pathlib import Path

from .cli import main  # noqa: F401  (re-exported for old entry points)
from .client import LabNexusClient
from .config import Settings, normalise_server
from .notify import Notifier
from .plain import prompt_login, report, run, setup_logging
from .watcher import ProbeEvent, Watcher

__all__ = ["FileWatcher", "main"]


def FileWatcher(dir_path: str, server_url: str) -> None:  # noqa: N802 - legacy name
    """Prompt for credentials and watch ``dir_path``, uploading to ``server_url``."""
    settings = Settings.single(
        Path(dir_path).expanduser(),
        normalise_server(server_url),
    )
    settings.state_file = settings.default_state_file()

    setup_logging()
    client = LabNexusClient(settings.server, timeout=settings.timeout, retries=settings.retries)
    prompt_login(client)

    notifier = Notifier(settings.notify)

    def on_event(event: ProbeEvent) -> None:
        report(event)
        if event.kind == "uploaded":
            notifier.send("pyProbe - upload complete", f"{event.name} is on the server.")

    try:
        run(Watcher(settings, client, on_event=on_event))
    finally:
        client.close()
