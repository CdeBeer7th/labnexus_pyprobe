"""Tkinter desktop front end.

Kept deliberately dependency-free: everything here is stdlib, so ``--gui`` works
on any Python that was built with Tk support.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import filedialog, ttk

from labnexus_plate_parsers import SpectrometerModel, resolve_model

from .client import AuthError, CaptchaRequired, LabNexusClient, UploadError, Workspace
from .config import DEFAULT_EXCLUDES, HttpDisabledError, Queue, Settings, normalise_server
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
    "parsed": ("parsed", ACCENT),
}

#: Shown in the per-queue instrument dropdown; "" means upload without parsing.
NO_MODEL = "(no parsing - upload as-is)"
MODEL_CHOICES = [NO_MODEL] + [m.value for m in SpectrometerModel]


@dataclass
class Session:
    """A signed-in LabNexus session, and what the sign-in told us about it."""

    client: LabNexusClient
    email: str
    workspaces: list[Workspace] = field(default_factory=list)
    #: Why the workspace list is empty, when the session itself is fine.
    workspace_error: str | None = None


def sign_in(
    server: str,
    email: str,
    *,
    pyprobe_token: str = "",
    password: str = "",
    timeout: float = 60.0,
    retries: int = 2,
    verify_tls: bool = True,
) -> Session:
    """Authenticate against *server* and read back the account's workspaces.

    Kept out of the window class so it is a plain function of its arguments:
    the window can call it on a worker thread, and it can be tested without a
    display. A pyProbe token wins over a password when both are given - it
    skips the CAPTCHA and second factor a password login may demand.

    Raises :class:`AuthError` (or :class:`CaptchaRequired`) if the credentials
    are refused. A session that authenticates but cannot list workspaces is
    still returned: the user can type a workspace id the dropdown never
    offered, so that failure is reported rather than fatal.
    """
    client = LabNexusClient(server, timeout=timeout, retries=retries, verify_tls=verify_tls)
    try:
        if pyprobe_token:
            who = client.authenticate_pyprobe(email, pyprobe_token)
        else:
            client.login(email, password)
            who = email
    except AuthError:
        client.close()
        raise

    try:
        return Session(client=client, email=who, workspaces=client.workspaces())
    except (AuthError, UploadError) as exc:
        return Session(client=client, email=who, workspace_error=str(exc))


class ProbeWindow(tk.Tk):
    """The pyProbe control window: configure a session, start it, watch it work."""

    def __init__(
        self,
        settings: Settings,
        email: str = "",
        pyprobe_token: str = "",
        version: str = "",
        scheme: str = "https",
        https_override: bool = False,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.version = version
        self.scheme = scheme
        self.https_override = https_override
        self.events: queue.Queue[ProbeEvent] = queue.Queue()
        #: Work a background thread wants done on the Tk thread. Tk widgets
        #: are not thread-safe, and ``after()`` from another thread is only
        #: safe while the main loop is actually running - draining a queue in
        #: :meth:`_drain` is true whatever the window is doing.
        self.ui_calls: queue.Queue[Callable[[], None]] = queue.Queue()
        self.watcher: Watcher | None = None
        self.worker: threading.Thread | None = None
        self.notifier = Notifier(settings.notify)
        #: The signed-in session, or None until the user signs in. Held on the
        #: window so one sign-in serves every start/stop of the watcher.
        self.session: Session | None = None
        self._signing_in = False
        self._start_when_signed_in = False

        self.title("pyProbe - LabNexus data sync")
        self.configure(bg=BG)
        self.geometry("860x620")
        self.minsize(720, 520)

        self._init_vars(email, pyprobe_token)
        self._init_style()
        self._build()
        self._focus_first_gap()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if email and pyprobe_token:
            # Everything an unattended sign-in needs came in on the command
            # line, so spend it now instead of waiting for the button.
            self.after(0, self._sign_in)

    # -- setup -----------------------------------------------------------

    def _init_vars(self, email: str, pyprobe_token: str = "") -> None:
        s = self.settings
        self.var_server = tk.StringVar(value=s.server)
        self.var_email = tk.StringVar(value=email)
        self.var_password = tk.StringVar()
        self.var_pyprobe_token = tk.StringVar(value=pyprobe_token)
        #: Both allows an http:// address and makes http the scheme assumed for
        #: a bare "host:port" - a server that speaks plain http wants both, and
        #: asking the user for them separately would be a trap.
        self.var_plain_http = tk.BooleanVar(value=self.https_override)
        self.var_server_hint = tk.StringVar()
        self.var_interval = tk.StringVar(value=str(int(s.interval)))
        self.var_recursive = tk.BooleanVar(value=s.queues[0].recursive if s.queues else False)
        self.var_workspace = tk.StringVar(value=s.workspace_id or "")
        #: Workspace name -> id, filled in once the server has been asked.
        self._workspace_ids: dict[str, str] = {}
        self.var_only_new = tk.BooleanVar(value=s.only_new)
        self.var_notify = tk.BooleanVar(value=s.notify)
        self.var_status = tk.StringVar(value="Idle - sign in, choose a folder, then press Start.")
        self.var_counts = tk.StringVar(value="")
        self.var_session = tk.StringVar(value="Not signed in")

        # Changing any of these invalidates the session they bought, so the
        # window drops it rather than uploading with a stale one.
        for var in (
            self.var_server,
            self.var_email,
            self.var_password,
            self.var_pyprobe_token,
            self.var_plain_http,
        ):
            var.trace_add("write", self._on_credentials_changed)
        self.var_plain_http.trace_add("write", self._refresh_server_hint)
        self._refresh_server_hint()

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
        if not self.var_server.get().strip():
            self.entry_server.focus_set()
            return
        if not self.var_email.get().strip():
            self.entry_email.focus_set()
            return
        if self.session is None:
            self.btn_sign_in.focus_set()
            return
        if not self.queue_rows:
            self.btn_add_queue.focus_set()
            return
        self.btn_start.focus_set()

    def _build_signin(self, parent: ttk.Frame) -> ttk.Frame:
        """The sign-in row: a button, its counterpart, and who is signed in."""
        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.columnconfigure(2, weight=1)
        self.btn_sign_in = ttk.Button(
            bar, text="Sign in", style="Browse.TButton", command=self._sign_in
        )
        self.btn_sign_in.grid(row=0, column=0, sticky="w")
        self.btn_sign_out = ttk.Button(
            bar,
            text="Sign out",
            style="Browse.TButton",
            command=self._sign_out,
            state="disabled",
        )
        self.btn_sign_out.grid(row=0, column=1, sticky="w", padx=(6, 10))
        ttk.Label(bar, textvariable=self.var_session, style="Hint.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        return bar

    def _build_form(self, parent: ttk.Frame) -> ttk.Frame:
        wrap = ttk.Frame(parent)
        wrap.columnconfigure(0, weight=1, uniform="col")
        wrap.columnconfigure(1, weight=1, uniform="col")

        conn = self._card(wrap, "Connection")
        conn.columnconfigure(1, weight=1)
        self.entry_server = self._field(conn, "Server", self.var_server, 1)
        server_row = ttk.Frame(conn, style="Card.TFrame")
        server_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 4))
        server_row.columnconfigure(0, weight=1)
        hint = ttk.Label(server_row, textvariable=self.var_server_hint, style="Hint.TLabel")
        hint.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        # The checkbox takes its width out of this row, leaving the hint less
        # than it wants; wrapping to the space it actually got beats clipping
        # the sentence mid-word.
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(120, event.width)))
        ttk.Checkbutton(server_row, text="Plain http", variable=self.var_plain_http).grid(
            row=0, column=1, sticky="e"
        )
        self.entry_email = self._field(conn, "Email", self.var_email, 3)
        self.entry_password = self._field(conn, "Password", self.var_password, 4, show="\u2022")
        self.entry_password.bind("<Return>", lambda _event: self._sign_in())
        entry_token = self._field(conn, "pyProbe token", self.var_pyprobe_token, 5, show="\u2022")
        entry_token.bind("<Return>", lambda _event: self._sign_in())
        self._hint(conn, "optional; from Settings -> pyProbe, used instead of the password", 6)

        conn.rowconfigure(7, minsize=8)
        self._build_signin(conn).grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(conn, text="Workspace", style="Field.TLabel").grid(
            row=9, column=0, sticky="w", padx=(0, 12), pady=4
        )
        # Populated by signing in; until then it stays an editable box so a
        # known id can just be pasted in.
        self.combo_workspace = ttk.Combobox(
            conn, textvariable=self.var_workspace, values=[], state="normal"
        )
        self.combo_workspace.grid(row=9, column=1, columnspan=2, sticky="ew", pady=4)
        self._hint(conn, "optional; left blank, parsed runs are filed under Pyprobe", 10)
        conn.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        watch = self._card(wrap, "Queues")
        watch.columnconfigure(0, weight=1)
        ttk.Label(
            watch,
            text="one folder per instrument - each is parsed before upload",
            style="Hint.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))

        self.queue_host = ttk.Frame(watch, style="Card.TFrame")
        self.queue_host.grid(row=2, column=0, columnspan=4, sticky="ew")
        self.queue_host.columnconfigure(0, weight=1)
        self.queue_rows: list[dict] = []

        self.btn_add_queue = ttk.Button(
            watch, text="+ Add folder", style="Browse.TButton", command=self._add_queue_row
        )
        self.btn_add_queue.grid(row=3, column=0, sticky="w", pady=(8, 0))

        opts = ttk.Frame(watch, style="Card.TFrame")
        opts.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(opts, text="Every (s)", style="Field.TLabel").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(opts, textvariable=self.var_interval, width=5).grid(row=0, column=1)
        for col, (text, var) in enumerate(
            (
                ("Subfolders", self.var_recursive),
                ("New files only", self.var_only_new),
                ("Notifications", self.var_notify),
            ),
            start=2,
        ):
            ttk.Checkbutton(opts, text=text, variable=var).grid(
                row=0, column=col, sticky="w", padx=(14, 0)
            )
        watch.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        for existing in self.settings.queues:
            self._add_queue_row(existing)
        return wrap

    # -- queue rows ------------------------------------------------------

    def _add_queue_row(self, queue: Queue | None = None) -> None:
        """Append one folder+instrument row, optionally prefilled from *queue*."""
        row = ttk.Frame(self.queue_host, style="Card.TFrame")
        row.grid(sticky="ew", pady=2)
        row.columnconfigure(0, weight=3)
        row.columnconfigure(1, weight=2)

        var_folder = tk.StringVar(value=str(queue.directory) if queue else "")
        var_model = tk.StringVar(
            value=queue.model.value if queue and queue.model else NO_MODEL
        )

        entry = ttk.Entry(row, textvariable=var_folder)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            row,
            text="...",
            style="Browse.TButton",
            width=3,
            command=lambda v=var_folder: self._pick_folder(v),
        ).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ttk.Combobox(
            row, textvariable=var_model, values=MODEL_CHOICES, state="readonly", width=26
        ).grid(row=0, column=2, sticky="ew", padx=(0, 6))

        record = {"frame": row, "folder": var_folder, "model": var_model}
        ttk.Button(
            row,
            text="\u2715",
            style="Browse.TButton",
            width=2,
            command=lambda r=record: self._remove_queue_row(r),
        ).grid(row=0, column=3, sticky="e")

        self.queue_rows.append(record)

    def _remove_queue_row(self, record: dict) -> None:
        record["frame"].destroy()
        self.queue_rows.remove(record)

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
        columns = ("time", "file", "instrument", "size", "status", "detail")
        self.tree = ttk.Treeview(wrap, columns=columns, show="headings", selectmode="browse")
        for name, width, anchor, stretch in (
            ("time", 80, "w", False),
            ("file", 220, "w", True),
            ("instrument", 170, "w", False),
            ("size", 80, "e", False),
            ("status", 80, "w", False),
            ("detail", 220, "w", True),
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
        self.status_label = ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel")
        self.status_label.pack(side="left", fill="x", expand=True)
        # Why a sign-in was refused takes a sentence or two to say properly, and
        # a clipped explanation is no explanation - so the bar wraps instead,
        # re-measured whenever the window is resized.
        bar.bind(
            "<Configure>",
            lambda event: self.status_label.configure(wraplength=max(200, event.width - 40)),
        )
        return bar

    # -- actions ---------------------------------------------------------

    def _pick_folder(self, var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(
            title="Choose the folder to watch", initialdir=var.get() or "."
        )
        if chosen:
            var.set(chosen)

    def _set_status(self, message: str, colour: str = MUTED) -> None:
        self.var_status.set(message)
        self.dot.itemconfigure("dot", fill=colour)

    # -- signing in ------------------------------------------------------

    def _refresh_server_hint(self, *_args) -> None:
        """Say which scheme a bare ``host:port`` will be given."""
        scheme = "http" if self.var_plain_http.get() else self.scheme
        self.var_server_hint.set(f"host, host:port or a full URL ({scheme}:// is assumed)")

    def _server_url(self) -> str | None:
        """The typed address as a base URL, or None with the reason on screen.

        Ticking "Plain http" both permits an http:// address and makes http the
        assumed scheme, so a bench pointed at a development server on port 8000
        does not silently get an https URL it can never connect to. An address
        typed with its own scheme keeps it either way.
        """
        raw = self.var_server.get().strip()
        if not raw:
            self._set_status("Enter the LabNexus server address.", ERR)
            return None
        plain_http = self.var_plain_http.get()
        scheme = "http" if plain_http else self.scheme
        try:
            return normalise_server(raw, scheme, allow_http=plain_http)
        except HttpDisabledError:
            # The CLI's advice ("pass --https-override true") is useless here;
            # in the window the same choice is a checkbox.
            self._set_status(
                f"{raw} is a plain http address. Tick 'Plain http' to allow it, "
                "or use an https:// server.",
                ERR,
            )
            return None

    def _credentials(self) -> tuple[str, str, str, str] | None:
        """Read server and credentials off the form, reporting the first gap."""
        server = self._server_url()
        if server is None:
            return None

        email = self.var_email.get().strip()
        if not email:
            self._set_status("Enter the email of your LabNexus account.", ERR)
            return None

        # A pyProbe token needs the email beside it, so both paths want one.
        token = self.var_pyprobe_token.get().strip()
        password = self.var_password.get()
        if not token and not password:
            self._set_status("Enter your password, or a pyProbe token.", ERR)
            return None
        return server, email, token, password

    def _sign_in(self, then_start: bool = False) -> None:
        """Authenticate and load the workspace list, off the Tk thread."""
        if self._signing_in:
            return
        credentials = self._credentials()
        if credentials is None:
            return
        server, email, token, password = credentials

        self._signing_in = True
        self._start_when_signed_in = then_start
        self.btn_sign_in.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.var_session.set("Signing in...")
        self._set_status(f"Signing in to {server}...", WARN)
        threading.Thread(
            target=self._do_sign_in,
            args=(server, email, token, password),
            daemon=True,
            name="pyprobe-signin",
        ).start()

    def _do_sign_in(self, server: str, email: str, token: str, password: str) -> None:
        """The network half of signing in. Runs on a worker thread."""
        try:
            session = sign_in(
                server,
                email,
                pyprobe_token=token,
                password=password,
                timeout=self.settings.timeout,
                retries=self.settings.retries,
                verify_tls=self.settings.verify_tls,
            )
        except CaptchaRequired as exc:
            self._on_ui_thread(self._signed_in_failed, str(exc))
        except AuthError as exc:
            self._on_ui_thread(self._signed_in_failed, str(exc))
        else:
            self._on_ui_thread(self._signed_in, session)

    def _signed_in(self, session: Session) -> None:
        """A live session arrived: remember it and show what it can reach."""
        self._signing_in = False
        self.session = session
        self.var_session.set(f"Signed in as {session.email}")
        self.btn_sign_in.configure(state="disabled")
        self.btn_sign_out.configure(state="normal")
        self.btn_start.configure(state="normal")
        self._apply_workspaces(session)

        client = session.client
        if session.workspace_error:
            self.events.put(
                ProbeEvent("info", "Could not load workspaces", detail=session.workspace_error)
            )
            self._set_status(
                f"Signed in as {session.email}, but the workspace list is unavailable.", WARN
            )
        else:
            self._set_status(
                f"Signed in as {session.email} - {len(session.workspaces)} workspace(s).", OK
            )
        self.events.put(
            ProbeEvent(
                "info",
                f"Signed in to {client.base_url} as {session.email}",
                detail=f"{len(session.workspaces)} workspace(s)",
            )
        )

        if self._start_when_signed_in:
            self._start_when_signed_in = False
            self._start()

    def _signed_in_failed(self, message: str) -> None:
        self._signing_in = False
        self._start_when_signed_in = False
        self.var_session.set("Not signed in")
        self.btn_sign_in.configure(state="normal")
        self.btn_start.configure(state="normal")
        self._set_status(message, ERR)
        self.events.put(ProbeEvent("error", "Sign-in failed", detail=message))

    def _apply_workspaces(self, session: Session) -> None:
        """Offer the fetched workspaces in the dropdown.

        A workspace id already in the box - typed, pasted or passed with
        ``--workspace`` - is swapped for its name now that the mapping is
        known, so the user sees where uploads are going. An empty box is left
        empty unless the server named a default: blank means "file it under
        the account's Pyprobe workspace", which is a real choice, not a gap.
        """
        client = session.client
        self._workspace_ids = {w.name: w.id for w in session.workspaces}
        if client.default_workspace_name and client.default_workspace_id:
            self._workspace_ids.setdefault(
                client.default_workspace_name, client.default_workspace_id
            )
        self.combo_workspace.configure(values=list(self._workspace_ids))

        current = self.var_workspace.get().strip()
        if not current:
            if client.default_workspace_name:
                self.var_workspace.set(client.default_workspace_name)
            return
        for name, workspace_id in self._workspace_ids.items():
            if current == workspace_id:
                self.var_workspace.set(name)
                return

    def _sign_out(self) -> None:
        if self.watcher is not None:
            self._set_status("Stop the sync before signing out.", WARN)
            return
        self._drop_session("Signed out.")

    def _drop_session(self, message: str) -> None:
        """Close the session and put the window back in its signed-out state."""
        session, self.session = self.session, None
        if session is not None:
            session.client.close()
        self.var_session.set("Not signed in")
        self.btn_sign_in.configure(state="normal")
        self.btn_sign_out.configure(state="disabled")
        self._set_status(message, MUTED)

    def _on_credentials_changed(self, *_args) -> None:
        """Editing the connection details invalidates the session they bought.

        A running sync keeps the session it started with - pulling the client
        out from under the watcher mid-upload would just fail the upload.
        """
        if self.session is None or self.watcher is not None:
            return
        self._drop_session("Connection details changed - sign in again.")

    def _collect(self) -> Settings | None:
        """Validate the form and fold it back into a Settings object."""
        if not self.var_server.get().strip():
            self._set_status("Enter the LabNexus server address.", ERR)
            return None
        try:
            interval = max(1.0, float(self.var_interval.get()))
        except ValueError:
            self._set_status("Interval must be a number of seconds.", ERR)
            return None

        queues = self._collect_queues()
        if queues is None:
            return None

        # Blank is allowed: the server files parsed runs under the account's
        # Pyprobe workspace when none is given.
        workspace = self._selected_workspace_id()

        server = self._server_url()
        if server is None:
            return None

        return replace(
            self.settings,
            server=server,
            queues=queues,
            workspace_id=workspace,
            interval=interval,
            only_new=self.var_only_new.get(),
            notify=self.var_notify.get(),
        )

    def _collect_queues(self) -> list[Queue] | None:
        """Read the queue rows, reporting the first unusable one."""
        queues: list[Queue] = []
        for record in self.queue_rows:
            raw = record["folder"].get().strip()
            if not raw:
                continue  # A blank row is one the user started and abandoned.
            folder = Path(raw).expanduser()
            if not folder.is_dir():
                self._set_status(f"Not a folder: {folder}", ERR)
                return None

            choice = record["model"].get()
            model = None if choice == NO_MODEL else resolve_model(choice)
            queues.append(
                Queue(
                    directory=folder,
                    model=model,
                    excludes=list(DEFAULT_EXCLUDES),
                    recursive=self.var_recursive.get(),
                )
            )

        if not queues:
            self._set_status("Add at least one folder to watch.", ERR)
            return None
        return queues

    def _selected_workspace_id(self) -> str | None:
        """Map the dropdown's label back to a workspace id.

        The box is editable, so it may hold a raw id the user pasted in before
        the list had loaded - that is passed through as-is.
        """
        chosen = self.var_workspace.get().strip()
        if not chosen:
            return None
        return self._workspace_ids.get(chosen, chosen)

    def _start(self) -> None:
        settings = self._collect()
        if settings is None:
            return
        if self.session is None:
            # Start doubles as Sign in for anyone who skipped the button.
            self._sign_in(then_start=True)
            return
        self.settings = settings
        self.notifier = Notifier(settings.notify)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_status("Connecting...", WARN)
        self.worker = threading.Thread(target=self._session, daemon=True, name="pyprobe-gui")
        self.worker.start()

    def _session(self) -> None:
        """Run the watcher on the signed-in session. Talks back via the queue.

        The session outlives one run: stopping and starting again reuses it,
        so a bench that pauses for an hour does not have to sign in twice.
        """
        session = self.session
        settings = self.settings
        if session is None:  # _start signs in first, so this is belt and braces.
            self.events.put(ProbeEvent("error", "Not signed in"))
            self.events.put(ProbeEvent("stopped", "Not connected"))
            return

        self.events.put(ProbeEvent("info", f"Watching for {session.email} on {settings.server}"))
        if not settings.workspace_id and session.client.default_workspace_name:
            self.events.put(
                ProbeEvent(
                    "info",
                    f"Parsed runs will be filed under {session.client.default_workspace_name}",
                )
            )
        # Held locally as well: _apply clears self.watcher the moment the
        # "stopped" event is drained, which happens on the other thread.
        watcher = Watcher(settings, session.client, on_event=self.events.put)
        self.watcher = watcher
        try:
            watcher.run()
        finally:
            self.events.put(ProbeEvent("stopped", "Sync stopped"))

        if not watcher.stopping:
            # It ended by itself rather than on Stop; an expired or revoked
            # session is the usual reason, so find out instead of leaving a
            # dead one on show.
            self._verify_session(session)

    def _verify_session(self, session: Session) -> None:
        """Drop *session* if the server has stopped accepting it."""
        try:
            session.client.workspaces()
        except AuthError:
            self._on_ui_thread(self._session_expired, session)
        except UploadError:
            pass  # The server is unreachable or unhappy; the session may be fine.

    def _session_expired(self, session: Session) -> None:
        """Runs on the Tk thread; ignores a session the user already replaced."""
        if self.session is session:
            self._drop_session("Session ended - sign in again.")

    def _stop(self) -> None:
        self._set_status("Stopping...", WARN)
        if self.watcher:
            self.watcher.stop()

    # -- event pump ------------------------------------------------------

    def _on_ui_thread(self, func: Callable[..., None], *args) -> None:
        """Queue *func* to run on the Tk thread. Safe to call from a worker."""
        self.ui_calls.put(lambda: func(*args))

    def _drain(self) -> None:
        """Move queued work and watcher events onto the widgets.

        Runs on the Tk thread every 100 ms - the one place background threads
        are allowed to reach the widgets, and the only place that touches them.
        """
        while True:
            try:
                call = self.ui_calls.get_nowait()
            except queue.Empty:
                break
            call()
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
                event.instrument,
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
            # The detail is the useful half of a failure ("HTTP 500", "token
            # expired"), so it goes in the status bar too, not just the table.
            self._set_status(
                f"{event.message} - {event.detail}" if event.detail else event.message, ERR
            )
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
        if self.session is not None:
            self.session.client.close()
            self.session = None
        self.destroy()


def run_gui(
    settings: Settings,
    email: str = "",
    pyprobe_token: str = "",
    version: str = "",
    scheme: str = "https",
    https_override: bool = False,
) -> int:
    """Entry point for ``--gui``. Returns a process exit code."""
    try:
        window = ProbeWindow(
            settings,
            email=email,
            pyprobe_token=pyprobe_token,
            version=version,
            scheme=scheme,
            https_override=https_override,
        )
    except tk.TclError as exc:
        print(f"Could not open a window ({exc}). Run without --gui for the terminal UI.")
        return 1
    window.mainloop()
    return 0
