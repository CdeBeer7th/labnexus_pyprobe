"""Runtime configuration for a pyProbe session."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

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
class Settings:
    """Everything the watcher needs to know, resolved from CLI args and the environment."""

    directory: Path
    server: str
    interval: float = 5.0
    patterns: list[str] = field(default_factory=lambda: ["*"])
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    recursive: bool = False
    only_new: bool = False
    min_age: float = 2.0
    reupload_changed: bool = True
    state_file: Path | None = None
    timeout: float = 60.0
    retries: int = 2
    verify_tls: bool = True
    notify: bool = True
    dry_run: bool = False

    def matches(self, path: Path) -> bool:
        """Whether *path* is a file this session should upload."""
        # Our own upload history can sit inside the watched directory; uploading
        # it would rewrite it on every scan and never settle.
        if self.state_file is not None and _same_file(path, self.state_file):
            return False
        name = path.name
        if any(fnmatch.fnmatch(name, pattern) for pattern in self.excludes):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.patterns)

    def iter_candidates(self) -> list[Path]:
        """All files under the watched directory that match the include/exclude filters."""
        walker = self.directory.rglob("*") if self.recursive else self.directory.glob("*")
        found = []
        for path in walker:
            try:
                if path.is_file() and self.matches(path):
                    found.append(path)
            except OSError:
                continue
        return found

    def default_state_file(self) -> Path:
        """Where upload history lives when the user did not pick a location."""
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        return base / "labnexus_pyprobe" / "uploads.json"
