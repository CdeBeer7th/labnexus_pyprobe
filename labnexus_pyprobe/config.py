"""Runtime configuration for a pyProbe session."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

from labnexus_plate_parsers import SpectrometerModel, patterns_for

#: Files that are almost never real experimental data.
DEFAULT_EXCLUDES = (
    ".*",
    "~$*",
    "*.tmp",
    "*.temp",
    "*.part",
    "*.crdownload",
    "*.swp",
    "Thumbs.db",
)


def _same_file(left: Path, right: Path) -> bool:
    """Path equality that tolerates symlinks and missing files."""
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def normalise_server(server: str, scheme: str = "http") -> str:
    """Turn ``host:port`` (or a full URL) into a base URL without a trailing slash."""
    server = server.strip()
    if "://" not in server:
        server = f"{scheme}://{server}"
    return server.rstrip("/")


@dataclass
class Queue:
    """One watched folder and, optionally, the instrument that writes into it.

    A queue with a ``model`` is parsed at the bench and uploaded as structured
    data; a queue without one is uploaded as a plain file. Separate folders are
    how a lab with several readers keeps their exports apart - each instrument
    gets its own drop folder and its own queue.
    """

    directory: Path
    model: SpectrometerModel | None = None
    patterns: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    recursive: bool = False

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).expanduser()

    @property
    def label(self) -> str:
        """Short human-readable name for logs and dashboards."""
        instrument = self.model.value if self.model else "any file"
        return f"{self.directory.name or self.directory} ({instrument})"

    @property
    def effective_patterns(self) -> list[str]:
        """What to match. Defaults to whatever the chosen instrument exports.

        Leaving patterns empty on a spectrometer queue is the useful default:
        a Tecan Spark folder should pick up ``*.xlsx`` without the user having
        to know that, and should not try to upload the operator's stray notes.
        """
        if self.patterns:
            return self.patterns
        if self.model is not None:
            return list(patterns_for(self.model))
        return ["*"]

    def matches(self, path: Path, state_file: Path | None = None) -> bool:
        """Whether *path* is a file this queue should upload."""
        # Our own upload history can sit inside a watched directory; uploading
        # it would rewrite it on every scan and never settle.
        if state_file is not None and _same_file(path, state_file):
            return False
        name = path.name
        if any(fnmatch.fnmatch(name, pattern) for pattern in self.excludes):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.effective_patterns)

    def iter_candidates(self, state_file: Path | None = None) -> list[Path]:
        """All files under this queue's directory that pass its filters."""
        walker = self.directory.rglob("*") if self.recursive else self.directory.glob("*")
        found = []
        for path in walker:
            try:
                if path.is_file() and self.matches(path, state_file):
                    found.append(path)
            except OSError:
                continue
        return found


@dataclass
class Settings:
    """Everything the watcher needs to know, resolved from CLI args and the environment."""

    server: str
    queues: list[Queue] = field(default_factory=list)
    #: Workspace every queue uploads into. Required for spectrometer queues,
    #: since that is what the server files the parsed run under.
    workspace_id: str | None = None
    interval: float = 5.0
    only_new: bool = False
    min_age: float = 2.0
    reupload_changed: bool = True
    state_file: Path | None = None
    timeout: float = 60.0
    retries: int = 2
    verify_tls: bool = True
    notify: bool = True
    dry_run: bool = False

    @classmethod
    def single(
        cls,
        directory: Path | str,
        server: str,
        *,
        model: SpectrometerModel | None = None,
        patterns: list[str] | None = None,
        excludes: list[str] | None = None,
        recursive: bool = False,
        **kwargs,
    ) -> Settings:
        """Build a one-queue session - the common case, and what the old API did."""
        queue = Queue(
            directory=Path(directory),
            model=model,
            patterns=list(patterns or []),
            excludes=list(excludes) if excludes is not None else list(DEFAULT_EXCLUDES),
            recursive=recursive,
        )
        return cls(server=server, queues=[queue], **kwargs)

    @property
    def spectrometer_queues(self) -> list[Queue]:
        return [q for q in self.queues if q.model is not None]

    def iter_candidates(self) -> list[tuple[Queue, Path]]:
        """Every uploadable file across every queue, tagged with its queue.

        A file reachable from two queues (nested folders, or the same folder
        listed twice) is yielded once, under the first queue that claims it -
        otherwise it would upload twice, once per instrument, and the second
        one would be parsed as the wrong vendor's format.
        """
        seen: set[Path] = set()
        found: list[tuple[Queue, Path]] = []
        for queue in self.queues:
            for path in queue.iter_candidates(self.state_file):
                try:
                    key = path.resolve()
                except OSError:
                    key = path
                if key in seen:
                    continue
                seen.add(key)
                found.append((queue, path))
        return found

    def default_state_file(self) -> Path:
        """Where upload history lives when the user did not pick a location."""
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        return base / "labnexus_pyprobe" / "uploads.json"
