"""Command-line entry point for pyProbe."""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

from .config import DEFAULT_EXCLUDES, Settings, normalise_server

PROG = "labnexus-pyprobe"

EPILOG = """\
environment variables:
  LABNEXUS_SERVER      default for --server
  LABNEXUS_DIR         default for --directory
  LABNEXUS_EMAIL       default for --email
  LABNEXUS_PASSWORD    password to use instead of prompting
  LABNEXUS_TOKEN       existing access token, skips the login step

examples:
  # watch a folder, prompt for credentials, live terminal dashboard
  labnexus-pyprobe ~/data lab.example.com:8000

  # only CSV and TXT files, including subfolders, checked every 30s
  labnexus-pyprobe -d ~/data -s lab.example.com -p '*.csv' -p '*.txt' -r -i 30

  # desktop window instead of the terminal
  labnexus-pyprobe --gui

  # see what would be uploaded without sending anything
  labnexus-pyprobe ~/data lab.example.com --dry-run

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
        help="front end to use (default: auto - tui on a terminal, plain otherwise)",
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


def resolve_ui(args: argparse.Namespace) -> str:
    if args.gui:
        return "gui"
    if args.plain:
        return "plain"
    if args.ui != "auto":
        return args.ui
    return "tui" if sys.stdout.isatty() else "plain"


def build_settings(args: argparse.Namespace, parser: argparse.ArgumentParser, ui: str) -> Settings:
    """Fold CLI args and environment into a Settings object, validating as we go."""
    raw_dir = args.directory or args.directory_pos or os.environ.get("LABNEXUS_DIR")
    raw_server = args.server or args.server_pos or os.environ.get("LABNEXUS_SERVER")

    # The GUI can collect anything that's missing, so only the CLI front ends
    # need these up front.
    if ui != "gui":
        if not raw_dir:
            parser.error("no directory given (pass DIRECTORY, --directory or set LABNEXUS_DIR)")
        if not raw_server:
            parser.error("no server given (pass SERVER, --server or set LABNEXUS_SERVER)")

    directory = Path(raw_dir).expanduser() if raw_dir else Path.cwd()
    if ui != "gui" and not directory.is_dir():
        parser.error(f"not a directory: {directory}")

    excludes = [] if args.no_default_excludes else list(DEFAULT_EXCLUDES)
    excludes.extend(args.exclude)

    settings = Settings(
        directory=directory,
        server=normalise_server(raw_server, args.scheme) if raw_server else "",
        interval=max(1.0, args.interval),
        patterns=args.pattern or ["*"],
        excludes=excludes,
        recursive=args.recursive,
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


def read_password(args: argparse.Namespace) -> str | None:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    return os.environ.get("LABNEXUS_PASSWORD")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui = resolve_ui(args)
    settings = build_settings(args, parser, ui)
    email = args.email or os.environ.get("LABNEXUS_EMAIL")
    token = args.token or os.environ.get("LABNEXUS_TOKEN")
    password = read_password(args)

    if ui == "gui":
        from .gui import run_gui

        return run_gui(settings, email=email or "", version=get_version())

    from .client import AuthError, LabNexusClient

    client = LabNexusClient(
        settings.server,
        timeout=settings.timeout,
        retries=settings.retries,
        verify_tls=settings.verify_tls,
    )

    try:
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
