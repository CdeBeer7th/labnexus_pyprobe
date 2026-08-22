"""The directory-watching engine that drives every front end."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .client import AuthError, LabNexusClient, UploadError
from .config import Settings

EventKind = str  # one of: info, scan, found, uploaded, failed, skipped, error


@dataclass
class ProbeEvent:
    """Something worth telling the user about. Front ends render these however they like."""

    kind: EventKind
    message: str
    path: Path | None = None
    size: int | None = None
    detail: str | None = None
    at: datetime = field(default_factory=datetime.now)

    @property
    def name(self) -> str:
        return self.path.name if self.path else ""


@dataclass
class Stats:
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_sent: int = 0
    scans: int = 0
    started_at: datetime = field(default_factory=datetime.now)


EventHandler = Callable[[ProbeEvent], None]


def file_digest(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's contents, so overwrites are detected rather than ignored."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class UploadHistory:
    """Remembers which files have already been sent, optionally across restarts."""

    def __init__(self, state_file: Path | None = None) -> None:
        self.state_file = state_file
        self._records: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            self._records = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self._records = {}

    def save(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._records, indent=2))
        except OSError:
            pass  # A read-only state dir shouldn't stop the sync.

    def seen(self, path: Path, digest: str | None) -> bool:
        record = self._records.get(str(path))
        if record is None:
            return False
        if digest is None:
            return True
        return record.get("digest") == digest

    def remember(self, path: Path, digest: str | None, size: int) -> None:
        self._records[str(path)] = {
            "digest": digest,
            "size": size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.save()

    def __len__(self) -> int:
        return len(self._records)


class Watcher:
    """Polls a directory and uploads anything new (or newly changed) to LabNexus."""

    def __init__(
        self,
        settings: Settings,
        client: LabNexusClient,
        on_event: EventHandler | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.on_event = on_event or (lambda event: None)
        self.history = UploadHistory(settings.state_file)
        self.stats = Stats()
        self._stop = threading.Event()

    # -- control ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def emit(self, kind: EventKind, message: str, **kwargs) -> None:
        self.on_event(ProbeEvent(kind=kind, message=message, **kwargs))

    # -- main loop -------------------------------------------------------

    def prime(self) -> int:
        """Mark everything currently on disk as already handled (``--only-new``)."""
        primed = 0
        for path in self.settings.iter_candidates():
            try:
                digest = file_digest(path) if self.settings.reupload_changed else None
                self.history.remember(path, digest, path.stat().st_size)
                primed += 1
            except OSError:
                continue
        return primed

    def run(self) -> Stats:
        """Scan until :meth:`stop` is called. Returns the final session statistics."""
        if self.settings.only_new:
            primed = self.prime()
            self.emit("info", f"Ignoring {primed} file(s) already in the directory.")

        while not self._stop.is_set():
            try:
                self.scan_once()
            except AuthError as exc:
                self.emit("error", str(exc))
                break
            except OSError as exc:
                self.emit("error", f"Cannot read {self.settings.directory}: {exc}")
            # Sleep in one interruptible chunk so Stop is instant.
            self._stop.wait(self.settings.interval)

        return self.stats

    def scan_once(self) -> None:
        """One pass over the directory, uploading every eligible file it finds."""
        self.stats.scans += 1
        candidates = sorted(self.settings.iter_candidates(), key=lambda p: p.stat().st_mtime)
        self.emit("scan", f"Scanned {self.settings.directory} - {len(candidates)} file(s) match.")

        for path in candidates:
            if self._stop.is_set():
                return
            self.process(path)

    def process(self, path: Path) -> None:
        """Decide whether *path* needs uploading, and upload it if so."""
        try:
            stat = path.stat()
        except OSError:
            return  # Vanished between listing and processing.

        # Give writers a moment to finish: an instrument still flushing data
        # would otherwise be uploaded half-written.
        if self.settings.min_age > 0 and (time.time() - stat.st_mtime) < self.settings.min_age:
            return

        try:
            digest = file_digest(path) if self.settings.reupload_changed else None
        except OSError as exc:
            self.stats.failed += 1
            self.emit("failed", f"Could not read {path.name}", path=path, detail=str(exc))
            return

        if self.history.seen(path, digest):
            return  # Already on the server and unchanged; nothing to report.

        if self.settings.dry_run:
            self.stats.skipped += 1
            self.emit("skipped", f"Would upload {path.name}", path=path, size=stat.st_size)
            self.history.remember(path, digest, stat.st_size)
            return

        self.emit("found", f"Uploading {path.name}", path=path, size=stat.st_size)
        try:
            self.client.upload(path)
        except UploadError as exc:
            self.stats.failed += 1
            self.emit("failed", f"Upload failed: {path.name}", path=path, detail=str(exc))
            return

        self.stats.uploaded += 1
        self.stats.bytes_sent += stat.st_size
        self.history.remember(path, digest, stat.st_size)
        self.emit("uploaded", f"Uploaded {path.name}", path=path, size=stat.st_size)
