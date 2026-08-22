"""Rich terminal front end: a live status dashboard for a sync session."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .client import AuthError, LabNexusClient
from .config import Settings
from .formatting import human_duration, human_size
from .watcher import ProbeEvent, Watcher

ACCENT = "bright_cyan"
MUTED = "grey58"

STATUS_STYLES = {
    "uploaded": ("[green]OK[/]", "green"),
    "failed": ("[red]FAIL[/]", "red"),
    "skipped": ("[yellow]DRY[/]", "yellow"),
    "found": ("[cyan]...[/]", "cyan"),
    "error": ("[red]ERR[/]", "red"),
    "info": ("[grey58]--[/]", MUTED),
    "scan": ("[grey58]--[/]", MUTED),
}

LOGO = r"""
             ____            _
  _ __  _   _|  _ \ _ __ ___ | |__   ___
 | '_ \| | | | |_) | '__/ _ \| '_ \ / _ \
 | |_) | |_| |  __/| | | (_) | |_) |  __/
 | .__/ \__, |_|   |_|  \___/|_.__/ \___|
 |_|    |___/
"""


def banner(console: Console, version: str) -> None:
    """Print the start-up splash."""
    console.print(Text(LOGO.strip("\n"), style=ACCENT))
    console.print(
        Text.assemble(
            ("  LabNexus data sync  ", MUTED),
            (f"v{version}", f"bold {ACCENT}"),
        )
    )
    console.print()


def prompt_login(console: Console, client: LabNexusClient, email: str | None = None) -> str:
    """Ask for credentials until the server accepts them. Returns the email used."""
    console.print(Rule(f"[{ACCENT}]Sign in[/] [{MUTED}]{client.base_url}[/]", style=MUTED))
    while True:
        user = email or Prompt.ask(f"  [{ACCENT}]email[/]")
        password = Prompt.ask(f"  [{ACCENT}]password[/]", password=True)
        try:
            client.login(user, password)
        except AuthError as exc:
            console.print(f"  [red]x[/] {exc}\n")
            email = None  # A wrong email shouldn't be re-used silently.
            continue
        console.print(f"  [green]OK[/] Session opened for [bold]{user}[/]\n")
        return user


class Dashboard:
    """Renders the live session view and pumps the watcher's events into it."""

    def __init__(
        self,
        settings: Settings,
        watcher: Watcher,
        console: Console,
        email: str = "",
        max_rows: int = 12,
    ) -> None:
        self.settings = settings
        self.watcher = watcher
        self.console = console
        self.email = email
        self.rows: deque[ProbeEvent] = deque(maxlen=max_rows)
        self.status = "starting up"
        self.last_scan: datetime | None = None
        self._lock = threading.Lock()

    # -- event sink ------------------------------------------------------

    def handle(self, event: ProbeEvent) -> None:
        with self._lock:
            if event.kind == "scan":
                self.last_scan = event.at
                self.status = "watching"
                return
            if event.kind == "found":
                self.status = f"uploading {event.name}"
                return
            self.rows.append(event)
            self.status = "watching"

    # -- rendering -------------------------------------------------------

    def header(self) -> Panel:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=MUTED, justify="right", no_wrap=True)
        grid.add_column(style="white", overflow="fold")

        patterns = " ".join(self.settings.patterns)
        excluded = len(self.settings.excludes)
        mode = "recursive" if self.settings.recursive else "top level"
        grid.add_row("server", f"[{ACCENT}]{self.settings.server}[/]")
        grid.add_row("watching", f"{self.settings.directory}  [{MUTED}]({mode})[/]")
        grid.add_row("matching", f"{patterns}  [{MUTED}]- {excluded} exclusion(s)[/]")
        grid.add_row(
            "session",
            f"{self.email or 'token'}  [green]connected[/]"
            + ("  [yellow](dry run)[/]" if self.settings.dry_run else ""),
        )
        return Panel(
            grid,
            title=f"[bold {ACCENT}]pyProbe[/]",
            title_align="left",
            border_style=ACCENT,
            padding=(0, 1),
        )

    def activity(self) -> Table:
        table = Table(
            box=None,
            expand=True,
            pad_edge=False,
            show_edge=False,
            header_style=f"bold {MUTED}",
        )
        table.add_column("time", style=MUTED, width=8, no_wrap=True)
        table.add_column("file", ratio=4, min_width=20, overflow="ellipsis", no_wrap=True)
        table.add_column("size", justify="right", width=9, style=MUTED)
        table.add_column("status", width=6)
        table.add_column("detail", ratio=3, style=MUTED, overflow="ellipsis", no_wrap=True)

        with self._lock:
            rows = list(self.rows)

        if not rows:
            table.add_row("", f"[{MUTED}]nothing uploaded yet[/]", "", "", "")
        for event in rows:
            label, style = STATUS_STYLES.get(event.kind, ("[grey58]--[/]", MUTED))
            name = event.name or event.message
            table.add_row(
                event.at.strftime("%H:%M:%S"),
                f"[{style}]{name}[/]",
                human_size(event.size) if event.size is not None else "",
                label,
                event.detail or "",
            )
        return table

    def footer(self, spinner: Spinner) -> Table:
        stats = self.watcher.stats
        elapsed = human_duration((datetime.now() - stats.started_at).total_seconds())
        summary = Text.assemble(
            (f"{stats.uploaded} uploaded", "green"),
            (" - ", MUTED),
            (f"{stats.failed} failed", "red" if stats.failed else MUTED),
            (" - ", MUTED),
            (f"{stats.skipped} skipped", MUTED),
            (" - ", MUTED),
            (human_size(stats.bytes_sent), MUTED),
            (" - ", MUTED),
            (f"up {elapsed}", MUTED),
        )
        if self.last_scan:
            remaining = self.settings.interval - (datetime.now() - self.last_scan).total_seconds()
            summary.append(f" - next scan {max(0, remaining):.0f}s", MUTED)

        bar = Table.grid(expand=True)
        bar.add_column(ratio=1)
        bar.add_column(justify="right")
        left = Table.grid(padding=(0, 1))
        left.add_column()
        left.add_column()
        left.add_row(spinner, Text(self.status + " ", style=ACCENT))
        bar.add_row(left, Group(summary))
        return bar

    def render(self, spinner: Spinner):
        return Group(
            self.header(),
            self.activity(),
            Rule(style=MUTED),
            self.footer(spinner),
            Text("  ctrl-c to stop", style=MUTED),
        )

    # -- driver ----------------------------------------------------------

    def run(self) -> None:
        """Run the watcher in a worker thread while the dashboard refreshes."""
        spinner = Spinner("dots", style=ACCENT)
        worker = threading.Thread(target=self.watcher.run, name="pyprobe-watcher", daemon=True)
        worker.start()

        with Live(
            self.render(spinner),
            console=self.console,
            refresh_per_second=10,
            transient=False,
        ) as live:
            try:
                while worker.is_alive():
                    live.update(self.render(spinner))
                    time.sleep(0.1)
            except KeyboardInterrupt:
                self.status = "stopping"
                self.watcher.stop()
                live.update(self.render(spinner))
                worker.join(timeout=10)
            finally:
                self.watcher.stop()
                live.update(self.render(spinner))

        stats = self.watcher.stats
        self.console.print(
            f"\n[{ACCENT}]Session closed.[/] "
            f"[green]{stats.uploaded} uploaded[/], "
            f"{stats.failed} failed, {human_size(stats.bytes_sent)} sent over "
            f"{human_duration((datetime.now() - stats.started_at).total_seconds())}."
        )
