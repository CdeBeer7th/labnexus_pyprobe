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

from labnexus_plate_parsers import UnifiedPlateReaderOutput, UnsupportedFileType
from labnexus_plate_parsers import parse as parse_plate_reader

from .client import AuthError, CaptchaRequired, LabNexusClient, UploadError
from .config import Queue, Settings

EventKind = str  # one of: info, scan, found, parsed, uploaded, failed, skipped, error


@dataclass
class ProbeEvent:
    """Something worth telling the user about. Front ends render these however they like."""

    kind: EventKind
    message: str
    path: Path | None = None
    size: int | None = None
    detail: str | None = None
    queue: Queue | None = None
    at: datetime = field(default_factory=datetime.now)

    @property
    def name(self) -> str:
        return self.path.name if self.path else ""

    @property
    def instrument(self) -> str:
        """The instrument this event's queue is watching for, if any."""
        if self.queue is None or self.queue.model is None:
            return ""
        return self.queue.model.value


@dataclass
class Stats:
    uploaded: int = 0
    failed: int = 0
    skipped: int = 0
    parsed: int = 0
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
    """Polls every configured queue and uploads anything new (or newly changed)."""

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
        for _queue, path in self.settings.iter_candidates():
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
            self.emit("info", f"Ignoring {primed} file(s) already in the watched folders.")

        while not self._stop.is_set():
            try:
                self.scan_once()
            except CaptchaRequired as exc:
                self.emit("info", f"{exc} Solving it automatically...")
                try:
                    self.client.reverify()
                except AuthError as reverify_exc:
                    self.emit("error", str(reverify_exc))
                    break
                self.emit("info", "CAPTCHA re-verified; resuming on the next scan.")
            except AuthError as exc:
                self.emit("error", str(exc))
                break
            except OSError as exc:
                self.emit("error", f"Cannot read a watched folder: {exc}")
            # Sleep in one interruptible chunk so Stop is instant.
            self._stop.wait(self.settings.interval)

        return self.stats

    def scan_once(self) -> None:
        """One pass over every queue, uploading each eligible file it finds."""
        self.stats.scans += 1
        candidates = sorted(
            self.settings.iter_candidates(), key=lambda pair: pair[1].stat().st_mtime
        )
        where = (
            str(self.settings.queues[0].directory)
            if len(self.settings.queues) == 1
            else f"{len(self.settings.queues)} queues"
        )
        self.emit("scan", f"Scanned {where} - {len(candidates)} file(s) match.")

        for queue, path in candidates:
            if self._stop.is_set():
                return
            self.process(queue, path)

    def process(self, queue: Queue, path: Path) -> None:
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
            self.emit(
                "failed", f"Could not read {path.name}", path=path, detail=str(exc), queue=queue
            )
            return

        if self.history.seen(path, digest):
            return  # Already on the server and unchanged; nothing to report.

        # Parse before the dry-run check so --dry-run still reports the parse
        # result: "would upload" is much less useful than "would upload, and
        # here is what it parsed to" when you are setting a queue up.
        structured: UnifiedPlateReaderOutput | None = None
        if queue.model is not None:
            structured = self.parse(queue, path)
            if structured is None:
                return  # parse() already recorded the failure.

        if self.settings.dry_run:
            self.stats.skipped += 1
            self.emit(
                "skipped", f"Would upload {path.name}", path=path, size=stat.st_size, queue=queue
            )
            self.history.remember(path, digest, stat.st_size)
            return

        self.emit("found", f"Uploading {path.name}", path=path, size=stat.st_size, queue=queue)
        try:
            if queue.model is not None:
                self.client.upload_spectrometer(
                    path,
                    queue.model,
                    self.settings.workspace_id or "",
                    structured=structured,
                )
            else:
                self.client.upload(path)
        except UploadError as exc:
            self.stats.failed += 1
            self.emit(
                "failed", f"Upload failed: {path.name}", path=path, detail=str(exc), queue=queue
            )
            return

        self.stats.uploaded += 1
        self.stats.bytes_sent += stat.st_size
        self.history.remember(path, digest, stat.st_size)
        self.emit("uploaded", f"Uploaded {path.name}", path=path, size=stat.st_size, queue=queue)

    def parse(self, queue: Queue, path: Path) -> UnifiedPlateReaderOutput | None:
        """Parse *path* as an export from this queue's instrument.

        Returns ``None`` and records a failure if it cannot be parsed - a file
        the bench cannot read is one the server would reject too, so there is
        no point spending the upload.
        """
        assert queue.model is not None
        try:
            structured = parse_plate_reader(queue.model, path.read_bytes(), path.name)
        except UnsupportedFileType as exc:
            # Reachable when the user pins explicit --pattern globs that let
            # through a file the instrument never exports.
            self.stats.failed += 1
            self.emit("failed", f"Skipping {path.name}", path=path, detail=str(exc), queue=queue)
            return None
        except OSError as exc:
            self.stats.failed += 1
            self.emit(
                "failed", f"Could not read {path.name}", path=path, detail=str(exc), queue=queue
            )
            return None
        except Exception as exc:
            self.stats.failed += 1
            self.emit(
                "failed",
                f"Could not parse {path.name}",
                path=path,
                detail=f"{queue.model.value}: {exc}",
                queue=queue,
            )
            return None

        self.stats.parsed += 1
        self.emit(
            "parsed",
            f"Parsed {path.name}",
            path=path,
            queue=queue,
            detail=(
                f"{len(structured.measurement_groups)} group(s), "
                f"{structured.measurement_count} series"
            ),
        )
        return structured
