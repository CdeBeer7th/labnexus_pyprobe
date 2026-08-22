"""Command-line entry point for pyProbe."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

from labnexus_plate_parsers import SpectrometerModel, UnknownModel, patterns_for, resolve_model

from .config import DEFAULT_EXCLUDES, Queue, Settings, normalise_server

PROG = "labnexus-pyprobe"

EPILOG = """\
environment variables:
  LABNEXUS_SERVER      default for --server
  LABNEXUS_DIR         default for --directory
  LABNEXUS_EMAIL       default for --email
  LABNEXUS_PASSWORD    password to use instead of prompting
  LABNEXUS_TOKEN       existing access token, skips the login step
  LABNEXUS_WORKSPACE   default for --workspace
  LABNEXUS_SPECTROMETER  default for --spectrometer

examples:
  # no arguments: open the desktop window and pick the folder and server there
  labnexus-pyprobe

  # watch a folder, prompt for credentials, live terminal dashboard
  labnexus-pyprobe ~/data lab.example.com:8000

  # only CSV and TXT files, including subfolders, checked every 30s
  labnexus-pyprobe -d ~/data -s lab.example.com -p '*.csv' -p '*.txt' -r -i 30

  # desktop window, prefilled with a folder and server
  labnexus-pyprobe --gui -d ~/data -s lab.example.com:8000

  # see what would be uploaded without sending anything
  labnexus-pyprobe ~/data lab.example.com --dry-run

  # one folder per instrument, each parsed at the bench before upload
  labnexus-pyprobe -s lab.example.com -w WORKSPACE_ID \\
      -Q ~/readers/spark=tecan-spark \\
      -Q ~/readers/epoch=biotek-epoch-2 \\
      -Q ~/readers/skanit=multiskan-skyhigh

  # a single spectrometer folder, the short way
  labnexus-pyprobe ~/readers/spark lab.example.com -m tecan-spark -w WORKSPACE_ID

  # which instruments are supported, and what each one exports
  labnexus-pyprobe --list-models

  # unattended, logging to a file, credentials from the environment
  LABNEXUS_PASSWORD=... labnexus-pyprobe ~/data lab.example.com \\
      -e me@lab.org --plain --log-file ~/pyprobe.log
"""


def get_version() -> str:
    try:
        return pkg_version("labnexus-pyprobe")
    except PackageNotFoundError:
        return "0.3.0+dev"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Watch a directory and upload new experimental data to a LabNexus server.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {get_version()}")

    # Positionals stay for backwards compatibility with `pyprobe DIR SERVER`.
    parser.add_argument(
        "directory_pos",
        nargs="?",
        metavar="DIRECTORY",
        help="directory to monitor (same as --directory)",
    )
    parser.add_argument(
        "server_pos",
        nargs="?",
        metavar="SERVER",
        help="LabNexus server, host[:port] or full URL (same as --server)",
    )

    conn = parser.add_argument_group("connection")
    conn.add_argument("-s", "--server", metavar="HOST[:PORT]", help="LabNexus server address")
    conn.add_argument(
        "--scheme",
        choices=("http", "https"),
        default="http",
        help="scheme to assume when SERVER has none (default: %(default)s)",
    )
    conn.add_argument("-e", "--email", metavar="ADDR", help="LabNexus account email")
    conn.add_argument("--token", metavar="JWT", help="use an existing access token, skip login")
    conn.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting",
    )
    conn.add_argument(
        "--timeout", type=float, default=60.0, metavar="SEC", help="HTTP timeout (default: 60)"
    )
    conn.add_argument(
        "--retries", type=int, default=2, metavar="N", help="retries per upload (default: 2)"
    )
    conn.add_argument(
        "-k", "--insecure", action="store_true", help="do not verify TLS certificates"
    )
    conn.add_argument(
        "--check",
        action="store_true",
        help="log in, report whether the server is reachable, then exit",
    )

    conn.add_argument(
        "-w",
        "--workspace",
        metavar="ID",
        help="workspace to file uploads under (required for spectrometer queues)",
    )
    conn.add_argument(
        "--list-workspaces",
        action="store_true",
        help="log in, print the workspaces you can upload into, then exit",
    )

    spec = parser.add_argument_group("spectrometers")
    spec.add_argument(
        "-Q",
        "--queue",
        action="append",
        default=[],
        metavar="PATH=MODEL",
        help=(
            "watch PATH for exports from MODEL; repeatable, one per instrument. "
            "MODEL is any spelling of a supported model (see --list-models)"
        ),
    )
    spec.add_argument(
        "-m",
        "--spectrometer",
        metavar="MODEL",
        help="instrument for the single folder given by DIRECTORY/--directory",
    )
    spec.add_argument(
        "--list-models",
        action="store_true",
        help="print the supported instruments and what each one exports, then exit",
    )

    watch = parser.add_argument_group("what to watch")
    watch.add_argument("-d", "--directory", metavar="PATH", help="directory to monitor")
    watch.add_argument(
        "-i",
        "--interval",
        type=float,
        default=5.0,
        metavar="SEC",
        help="seconds between scans (default: 5)",
    )
    watch.add_argument(
        "-p",
        "--pattern",
        action="append",
        default=[],
        metavar="GLOB",
        help="only upload files matching GLOB; repeatable (default: *)",
    )
    watch.add_argument(
        "-x",
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="never upload files matching GLOB; repeatable",
    )
    watch.add_argument(
        "--no-default-excludes",
        action="store_true",
        help=f"drop the built-in exclusions ({', '.join(DEFAULT_EXCLUDES)})",
    )
    watch.add_argument("-r", "--recursive", action="store_true", help="also watch subdirectories")
    watch.add_argument(
        "--only-new",
        action="store_true",
        help="ignore files that already exist when the session starts",
    )
    watch.add_argument(
        "--min-age",
        type=float,
        default=2.0,
        metavar="SEC",
        help="wait this long after the last write before uploading (default: 2)",
    )
    watch.add_argument(
        "--no-reupload-changed",
        action="store_true",
        help="do not re-upload a file whose contents changed after it was sent",
    )
    watch.add_argument(
        "--state-file",
        metavar="PATH",
        help="where to remember uploaded files across restarts",
    )
    watch.add_argument(
        "--no-state",
        action="store_true",
        help="keep upload history in memory only; a restart re-uploads everything",
    )

    ui = parser.add_argument_group("interface")
    ui.add_argument(
        "--ui",
        choices=("auto", "tui", "plain", "gui"),
        default="auto",
        help=(
            "front end to use (default: auto - gui when run with no arguments, "
            "tui on a terminal, plain otherwise)"
        ),
    )
    ui.add_argument("-g", "--gui", action="store_true", help="shorthand for --ui gui")
    ui.add_argument("--plain", action="store_true", help="shorthand for --ui plain")
    ui.add_argument("--no-notify", action="store_true", help="suppress desktop notifications")
    ui.add_argument("-v", "--verbose", action="count", default=0, help="more log detail (plain UI)")
    ui.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    ui.add_argument("--log-file", metavar="PATH", help="also append log output to PATH")
    ui.add_argument(
        "-n", "--dry-run", action="store_true", help="report what would be uploaded, send nothing"
    )
    return parser


def gui_available() -> bool:
    """Whether a desktop window can plausibly be opened on this machine."""
    try:
        import tkinter  # noqa: F401
    except Exception:  # pragma: no cover - depends on how Python was built
        return False
    if sys.platform.startswith(("linux", "freebsd")):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def resolve_ui(args: argparse.Namespace, bare: bool = False) -> str:
    """Pick a front end. *bare* means the command was run with no arguments at all."""
    if args.gui:
        return "gui"
    if args.plain:
        return "plain"
    if args.ui != "auto":
        return args.ui
    # Someone who just double-clicked the app, or typed the bare command, has
    # nothing to configure a session with - give them the window that can ask.
    if bare and gui_available():
        return "gui"
    return "tui" if sys.stdout.isatty() else "plain"


def parse_queue_spec(
    spec: str, parser: argparse.ArgumentParser
) -> tuple[Path, SpectrometerModel | None]:
    """Split one ``--queue PATH=MODEL`` value.

    The model half is optional (``--queue ~/misc`` watches a folder with no
    parsing), and ``rsplit`` is deliberate so a Windows path such as
    ``C:\\data=tecan-spark`` splits on the right ``=``.
    """
    raw_path, sep, raw_model = spec.rpartition("=")
    if not sep:
        raw_path, raw_model = spec, ""

    path = Path(raw_path.strip()).expanduser()
    if not str(path).strip():
        parser.error(f"--queue needs a folder: {spec!r}")

    if not raw_model.strip():
        return path, None
    try:
        return path, resolve_model(raw_model.strip())
    except UnknownModel as exc:
        parser.error(f"--queue {spec!r}: {exc}")
        raise  # unreachable; parser.error exits


def build_queues(
    args: argparse.Namespace, parser: argparse.ArgumentParser, ui: str
) -> list[Queue]:
    """Fold --queue, --directory and --spectrometer into the list of watched folders."""
    excludes = [] if args.no_default_excludes else list(DEFAULT_EXCLUDES)
    excludes.extend(args.exclude)

    def make(directory: Path, model: SpectrometerModel | None) -> Queue:
        return Queue(
            directory=directory,
            model=model,
            # An explicit --pattern wins; otherwise a spectrometer queue takes
            # the instrument's own extensions and a plain queue takes "*".
            patterns=list(args.pattern),
            excludes=list(excludes),
            recursive=args.recursive,
        )

    queues = [make(path, model) for path, model in
              (parse_queue_spec(spec, parser) for spec in args.queue)]

    raw_dir = args.directory or args.directory_pos or os.environ.get("LABNEXUS_DIR")
    raw_model = args.spectrometer or os.environ.get("LABNEXUS_SPECTROMETER")

    if raw_dir:
        model = None
        if raw_model:
            try:
                model = resolve_model(raw_model)
            except UnknownModel as exc:
                parser.error(str(exc))
        queues.append(make(Path(raw_dir).expanduser(), model))
    elif raw_model and not queues:
        parser.error("--spectrometer needs a folder (pass DIRECTORY or --directory)")

    if not queues:
        # The GUI can collect a folder itself; the CLI front ends cannot.
        if ui != "gui":
            parser.error(
                "nothing to watch (pass DIRECTORY, --directory, --queue, or set LABNEXUS_DIR)"
            )
        queues.append(make(Path.cwd(), None))

    if ui != "gui":
        for queue in queues:
            if not queue.directory.is_dir():
                parser.error(f"not a directory: {queue.directory}")

    return queues


def build_settings(args: argparse.Namespace, parser: argparse.ArgumentParser, ui: str) -> Settings:
    """Fold CLI args and environment into a Settings object, validating as we go."""
    raw_server = args.server or args.server_pos or os.environ.get("LABNEXUS_SERVER")
    if ui != "gui" and not raw_server:
        parser.error("no server given (pass SERVER, --server or set LABNEXUS_SERVER)")

    queues = build_queues(args, parser, ui)
    workspace = args.workspace or os.environ.get("LABNEXUS_WORKSPACE")

    # A parsed run is filed against a workspace; without one the server has
    # nowhere to put it, and failing here beats failing on every upload.
    if ui != "gui" and not workspace and any(q.model for q in queues):
        parser.error(
            "spectrometer queues need a workspace (pass --workspace or set "
            "LABNEXUS_WORKSPACE; --list-workspaces shows the ones you can use)"
        )

    settings = Settings(
        server=normalise_server(raw_server, args.scheme) if raw_server else "",
        queues=queues,
        workspace_id=workspace,
        interval=max(1.0, args.interval),
        only_new=args.only_new,
        min_age=max(0.0, args.min_age),
        reupload_changed=not args.no_reupload_changed,
        timeout=args.timeout,
        retries=max(0, args.retries),
        verify_tls=not args.insecure,
        notify=not args.no_notify,
        dry_run=args.dry_run,
    )

    if args.no_state:
        settings.state_file = None
    elif args.state_file:
        settings.state_file = Path(args.state_file).expanduser()
    else:
        settings.state_file = settings.default_state_file()

    return settings


def print_models() -> int:
    """Answer "which instruments can this thing handle?" without a server."""
    width = max(len(m.name) for m in SpectrometerModel)
    print("Supported spectrometers (any spelling of either column is accepted):\n")
    for model in SpectrometerModel:
        globs = " ".join(patterns_for(model))
        print(f"  {model.name:<{width}}  {model.value:<38}  {globs}")
    print("\nUse with:  --queue PATH=MODEL   or   --spectrometer MODEL")
    return 0


def read_password(args: argparse.Namespace) -> str | None:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    return os.environ.get("LABNEXUS_PASSWORD")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Answerable offline, so it runs before anything needs a server or a folder.
    if args.list_models:
        return print_models()

    ui = resolve_ui(args, bare=not argv)
    settings = build_settings(args, parser, ui)
    email = args.email or os.environ.get("LABNEXUS_EMAIL")
    token = args.token or os.environ.get("LABNEXUS_TOKEN")
    password = read_password(args)

    if ui == "gui":
        from .gui import run_gui

        return run_gui(
            settings,
            email=email or "",
            version=get_version(),
            scheme=args.scheme,
        )

    from .client import AuthError, LabNexusClient

    client = LabNexusClient(
        settings.server,
        timeout=settings.timeout,
        retries=settings.retries,
        verify_tls=settings.verify_tls,
    )

    try:
        if args.list_workspaces:
            return print_workspaces(client, email, token, password)
        if ui == "tui":
            return run_tui(settings, client, email, token, password, args)
        return run_plain(settings, client, email, token, password, args)
    except AuthError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        client.close()


def _authenticate(client, email, token, password, login_prompt) -> str:
    """Use a token or non-interactive credentials if we have them, else prompt."""
    if token:
        client.use_token(token)
        return email or "token"
    if email and password:
        client.login(email, password)
        return email
    return login_prompt(email)


def print_workspaces(client, email, token, password) -> int:
    """List the workspaces this account can upload into, with their ids."""
    from .plain import prompt_login

    _authenticate(client, email, token, password, lambda e: prompt_login(client, e))
    spaces = client.workspaces()
    if not spaces:
        print("No workspaces available for this account.")
        return 1

    width = max(len(w.name) for w in spaces)
    for workspace in spaces:
        role = "owner" if workspace.owned else "shared"
        print(f"  {workspace.name:<{width}}  {workspace.id}  ({role})")
    print("\nUse with:  --workspace ID")
    return 0


def run_tui(settings, client, email, token, password, args) -> int:
    from rich.console import Console

    from .tui import Dashboard, banner, prompt_login
    from .watcher import Watcher

    console = Console()
    banner(console, get_version())
    who = _authenticate(
        client, email, token, password, lambda e: prompt_login(console, client, e)
    )

    if args.check:
        console.print(f"[green]OK[/] {settings.server} reachable and credentials accepted.")
        return 0

    watcher = Watcher(settings, client)
    dashboard = Dashboard(settings, watcher, console, email=who)
    watcher.on_event = dashboard.handle
    dashboard.run()
    return 1 if watcher.stats.failed else 0


def run_plain(settings, client, email, token, password, args) -> int:
    from .notify import Notifier
    from .plain import prompt_login, report, run, setup_logging
    from .watcher import ProbeEvent, Watcher

    setup_logging(args.verbose, args.quiet, args.log_file)
    _authenticate(client, email, token, password, lambda e: prompt_login(client, e))

    if args.check:
        print(f"OK: {settings.server} reachable and credentials accepted.")
        return 0

    notifier = Notifier(settings.notify)

    def on_event(event: ProbeEvent) -> None:
        report(event)
        if event.kind == "uploaded":
            notifier.send("pyProbe - upload complete", f"{event.name} is on the server.")
        elif event.kind in ("failed", "error"):
            notifier.send("pyProbe - problem", f"{event.message}: {event.detail or ''}")

    watcher = Watcher(settings, client, on_event=on_event)
    run(watcher)
    return 1 if watcher.stats.failed else 0


def main_gui(argv: list[str] | None = None) -> int:
    """Windowed entry point: same CLI, but defaults to the desktop front end."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a == "--gui" or a.startswith("--ui") for a in argv):
        argv.append("--gui")
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
