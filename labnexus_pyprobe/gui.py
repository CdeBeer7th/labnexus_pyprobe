"""Tkinter desktop front end.

Kept deliberately dependency-free: everything here is stdlib, so ``--gui`` works
on any Python that was built with Tk support.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, ttk

from .client import AuthError, LabNexusClient
from .config import DEFAULT_EXCLUDES, Settings, normalise_server
from .formatting import human_size
from .notify import Notifier
from .watcher import ProbeEvent, Watcher

# A calm dark palette; the accent is the only saturated colour on screen.
BG = "#16191d"
PANEL = "#1e2228"
FIELD = "#12151a"
BORDER = "#2b313a"
TEXT = "#e6eaf0"
MUTED = "#8b95a5"
ACCENT = "#22d3ee"
OK = "#4ade80"
ERR = "#f87171"
WARN = "#fbbf24"

STATUS_TAGS = {
    "uploaded": ("uploaded", OK),
    "failed": ("failed", ERR),
    "error": ("failed", ERR),
    "skipped": ("skipped", WARN),
    "info": ("info", MUTED),
    "found": ("info", ACCENT),
}


class ProbeWindow(tk.Tk):
    """The pyProbe control window: configure a session, start it, watch it work."""

    def __init__(
        self,
        settings: Settings,
        email: str = "",
        version: str = "",
        scheme: str = "http",
    ) -> None:
        super().__init__()
        self.settings = settings
        self.version = version
        self.scheme = scheme
        self.events: queue.Queue[ProbeEvent] = queue.Queue()
        self.watcher: Watcher | None = None
        self.worker: threading.Thread | None = None
        self.notifier = Notifier(settings.notify)

        self.title("pyProbe - LabNexus data sync")
        self.configure(bg=BG)
        self.geometry("860x620")
        self.minsize(720, 520)

        self._init_vars(email)
        self._init_style()
        self._build()
        self._focus_first_gap()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- setup -----------------------------------------------------------

    def _init_vars(self, email: str) -> None:
        s = self.settings
        self.var_server = tk.StringVar(value=s.server)
        self.var_email = tk.StringVar(value=email)
        self.var_password = tk.StringVar()
        self.var_folder = tk.StringVar(value=str(s.directory))
        self.var_patterns = tk.StringVar(value=" ".join(s.patterns))
        self.var_interval = tk.StringVar(value=str(int(s.interval)))
        self.var_recursive = tk.BooleanVar(value=s.recursive)
        self.var_only_new = tk.BooleanVar(value=s.only_new)
        self.var_notify = tk.BooleanVar(value=s.notify)
        self.var_status = tk.StringVar(value="Idle - choose a folder and server, then press Start.")
        self.var_counts = tk.StringVar(value="")

    def _init_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure(
            "Field.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9)
        )
        style.configure(
            "Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 20, "bold")
        )
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure(
            "Section.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 9, "bold")
        )
        style.configure("Status.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8))

        style.configure(
            "TEntry",
            fieldbackground=FIELD,
            background=FIELD,
            foreground=TEXT,
            bordercolor=BORDER,
            insertcolor=ACCENT,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=6,
        )
        style.map("TEntry", bordercolor=[("focus", ACCENT)])

        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#04222a",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 8),
        )
        style.map("Accent.TButton", background=[("active", "#67e8f9"), ("disabled", BORDER)])
        style.configure(
            "Ghost.TButton",
            background=PANEL,
            foreground=TEXT,
            bordercolor=BORDER,
            padding=(18, 8),
        )
        style.map("Ghost.TButton", background=[("active", BORDER), ("disabled", PANEL)])
        style.configure(
            "Browse.TButton", background=BORDER, foreground=TEXT, padding=(12, 5)
        )
        style.map("Browse.TButton", background=[("active", ACCENT)])

        style.configure(
            "TCheckbutton",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
            indicatorbackground=FIELD,
            indicatorforeground=ACCENT,
            focuscolor=PANEL,
        )
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])

        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=24,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=BG,
            foreground=MUTED,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", BG)])
        style.map(
            "Treeview",
            background=[("selected", BORDER)],
            foreground=[("selected", TEXT)],
        )

    # -- layout ----------------------------------------------------------

    def _build(self) -> None:
        root = ttk.Frame(self, padding=(20, 16))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        self._build_header(root).grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._build_form(root).grid(row=1, column=0, sticky="ew")
        self._build_actions(root).grid(row=2, column=0, sticky="ew", pady=(14, 10))
        self._build_activity(root).grid(row=3, column=0, sticky="nsew")
        self._build_statusbar(root).grid(row=4, column=0, sticky="ew", pady=(10, 0))

    def _build_header(self, parent: ttk.Frame) -> ttk.Frame:
        head = ttk.Frame(parent)
        ttk.Label(head, text="pyProbe", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text=f"Automatic LabNexus data sync   v{self.version}",
            style="Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Frame(head, background=ACCENT, height=2).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )
        head.columnconfigure(0, weight=1)
        return head

    def _card(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 12, 16, 14))
        ttk.Label(card, text=title.upper(), style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )
        return card

    def _field(self, card: ttk.Frame, label: str, var: tk.StringVar, row: int, **kw) -> ttk.Entry:
        span = kw.pop("span", 2)
        ttk.Label(card, text=label, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=4
        )
        entry = ttk.Entry(card, textvariable=var, **kw)
        entry.grid(row=row, column=1, columnspan=span, sticky="ew", pady=4)
        return entry

    def _hint(self, card: ttk.Frame, text: str, row: int) -> None:
        ttk.Label(card, text=text, style="Hint.TLabel").grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, 4)
        )

    def _focus_first_gap(self) -> None:
        """Put the cursor in the first thing the user still has to fill in."""
        gaps = (
            (self.entry_server, self.var_server),
            (self.entry_folder, self.var_folder),
        )
        for entry, var in gaps:
            if not var.get().strip():
                entry.focus_set()
                return
        self.btn_start.focus_set()

    def _build_form(self, parent: ttk.Frame) -> ttk.Frame:
        wrap = ttk.Frame(parent)
        wrap.columnconfigure(0, weight=1, uniform="col")
        wrap.columnconfigure(1, weight=1, uniform="col")

        conn = self._card(wrap, "Connection")
        conn.columnconfigure(1, weight=1)
        self.entry_server = self._field(conn, "Server", self.var_server, 1)
        self._hint(conn, f"host, host:port or a full URL ({self.scheme}:// is assumed)", 2)
        self._field(conn, "Email", self.var_email, 3)
        self._field(conn, "Password", self.var_password, 4, show="•")
        conn.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        watch = self._card(wrap, "Watch")
        watch.columnconfigure(1, weight=1)
        self.entry_folder = self._field(watch, "Folder", self.var_folder, 1, span=1)
        ttk.Button(watch, text="Browse", style="Browse.TButton", command=self._pick_folder).grid(
            row=1, column=2, sticky="e", padx=(8, 0), pady=4
        )
        self._hint(watch, "every new file dropped in here is uploaded", 2)
        self._field(watch, "Patterns", self.var_patterns, 3)
        self._field(watch, "Every (s)", self.var_interval, 4, width=6, span=1)

        toggles = ttk.Frame(watch, style="Card.TFrame")
        toggles.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for col, (text, var) in enumerate(
            (
                ("Subfolders", self.var_recursive),
                ("New files only", self.var_only_new),
                ("Notifications", self.var_notify),
            )
        ):
            ttk.Checkbutton(toggles, text=text, variable=var).grid(
                row=0, column=col, sticky="w", padx=(0, 14)
            )
        watch.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        return wrap

    def _build_actions(self, parent: ttk.Frame) -> ttk.Frame:
        bar = ttk.Frame(parent)
        self.btn_start = ttk.Button(
            bar, text="Start syncing", style="Accent.TButton", command=self._start
        )
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(
            bar, text="Stop", style="Ghost.TButton", command=self._stop, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=(10, 0))
        ttk.Label(bar, textvariable=self.var_counts, style="Sub.TLabel").pack(
            side="right", pady=(6, 0)
        )
        return bar

    def _build_activity(self, parent: ttk.Frame) -> ttk.Frame:
        wrap = ttk.Frame(parent, style="Card.TFrame", padding=(2, 2))
        columns = ("time", "file", "size", "status", "detail")
        self.tree = ttk.Treeview(wrap, columns=columns, show="headings", selectmode="browse")
        for name, width, anchor, stretch in (
            ("time", 80, "w", False),
            ("file", 260, "w", True),
            ("size", 90, "e", False),
            ("status", 90, "w", False),
            ("detail", 240, "w", True),
        ):
            self.tree.heading(name, text=name.title(), anchor=anchor)
            self.tree.column(name, width=width, anchor=anchor, stretch=stretch)

        for tag, colour in {v[0]: v[1] for v in STATUS_TAGS.values()}.items():
            self.tree.tag_configure(tag, foreground=colour)

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return wrap

    def _build_statusbar(self, parent: ttk.Frame) -> ttk.Frame:
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 7))
        self.dot = tk.Canvas(bar, width=9, height=9, bg=PANEL, highlightthickness=0)
        self.dot.create_oval(1, 1, 8, 8, fill=MUTED, outline="", tags="dot")
        self.dot.pack(side="left", padx=(0, 8))
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel").pack(side="left")
        return bar

    # -- actions ---------------------------------------------------------

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="Choose the folder to watch", initialdir=self.var_folder.get() or "."
        )
        if chosen:
            self.var_folder.set(chosen)

    def _set_status(self, message: str, colour: str = MUTED) -> None:
        self.var_status.set(message)
        self.dot.itemconfigure("dot", fill=colour)

    def _collect(self) -> Settings | None:
        """Validate the form and fold it back into a Settings object."""
        folder = Path(self.var_folder.get()).expanduser()
        if not folder.is_dir():
            self._set_status(f"Not a folder: {folder}", ERR)
            return None
        if not self.var_server.get().strip():
            self._set_status("Enter the LabNexus server address.", ERR)
            return None
        try:
            interval = max(1.0, float(self.var_interval.get()))
        except ValueError:
            self._set_status("Interval must be a number of seconds.", ERR)
            return None

        patterns = self.var_patterns.get().split() or ["*"]
        return replace(
            self.settings,
            directory=folder,
            server=normalise_server(self.var_server.get(), self.scheme),
            interval=interval,
            patterns=patterns,
            excludes=list(self.settings.excludes or DEFAULT_EXCLUDES),
            recursive=self.var_recursive.get(),
            only_new=self.var_only_new.get(),
            notify=self.var_notify.get(),
        )

    def _start(self) -> None:
        settings = self._collect()
        if settings is None:
            return
        self.settings = settings
        self.notifier = Notifier(settings.notify)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_status("Connecting...", WARN)
        self.worker = threading.Thread(target=self._session, daemon=True, name="pyprobe-gui")
        self.worker.start()

    def _session(self) -> None:
        """Log in and run the watcher. Runs off the Tk thread; talks back via the queue."""
        settings = self.settings
        client = LabNexusClient(
            settings.server,
            timeout=settings.timeout,
            retries=settings.retries,
            verify_tls=settings.verify_tls,
        )
        try:
            client.login(self.var_email.get().strip(), self.var_password.get())
        except AuthError as exc:
            self.events.put(ProbeEvent("error", "Login failed", detail=str(exc)))
            self.events.put(ProbeEvent("stopped", "Not connected"))
            return

        self.events.put(ProbeEvent("info", f"Connected to {settings.server}"))
        self.watcher = Watcher(settings, client, on_event=self.events.put)
        try:
            self.watcher.run()
        finally:
            client.close()
            self.events.put(ProbeEvent("stopped", "Session closed"))

    def _stop(self) -> None:
        self._set_status("Stopping...", WARN)
        if self.watcher:
            self.watcher.stop()

    # -- event pump ------------------------------------------------------

    def _drain(self) -> None:
        """Move watcher events onto the widgets. Runs on the Tk thread every 100 ms."""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._apply(event)
        self._refresh_counts()
        self.after(100, self._drain)

    def _apply(self, event: ProbeEvent) -> None:
        if event.kind == "stopped":
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self._set_status(event.message, MUTED)
            self.watcher = None
            return
        if event.kind == "scan":
            self._set_status(event.message, ACCENT)
            return
        if event.kind == "found":
            self._set_status(f"Uploading {event.name}...", ACCENT)
            return

        tag, _ = STATUS_TAGS.get(event.kind, ("info", MUTED))
        self.tree.insert(
            "",
            "end",
            values=(
                event.at.strftime("%H:%M:%S"),
                event.name or event.message,
                human_size(event.size) if event.size is not None else "",
                event.kind,
                event.detail or "",
            ),
            tags=(tag,),
        )
        self.tree.yview_moveto(1.0)

        if event.kind == "uploaded":
            self._set_status(f"Uploaded {event.name}", OK)
            self.notifier.send("pyProbe - upload complete", f"{event.name} is on the server.")
        elif event.kind in ("failed", "error"):
            self._set_status(event.message, ERR)
            self.notifier.send("pyProbe - problem", f"{event.message}: {event.detail or ''}")

    def _refresh_counts(self) -> None:
        if not self.watcher:
            return
        stats = self.watcher.stats
        self.var_counts.set(
            f"{stats.uploaded} uploaded  -  {stats.failed} failed  -  "
            f"{human_size(stats.bytes_sent)} sent"
        )

    def _on_close(self) -> None:
        if self.watcher:
            self.watcher.stop()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)
        self.destroy()


def run_gui(
    settings: Settings, email: str = "", version: str = "", scheme: str = "http"
) -> int:
    """Entry point for ``--gui``. Returns a process exit code."""
    try:
        window = ProbeWindow(settings, email=email, version=version, scheme=scheme)
    except tk.TclError as exc:
        print(f"Could not open a window ({exc}). Run without --gui for the terminal UI.")
        return 1
    window.mainloop()
    return 0
