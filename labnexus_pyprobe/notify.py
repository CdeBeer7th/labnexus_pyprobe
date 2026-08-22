"""Cross-platform desktop notifications, with a silent fallback everywhere else."""

from __future__ import annotations

import platform
import shutil
import subprocess

APP_NAME = "pyProbe"


class Notifier:
    """Sends desktop notifications if the platform offers a way to; otherwise does nothing.

    Notifications are a nicety, never a hard dependency - every backend failure is
    swallowed so a missing toast library can't take the watcher down with it.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._backend = self._pick_backend() if enabled else None

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def backend(self) -> str:
        return self._backend or "none"

    def _pick_backend(self) -> str | None:
        system = platform.system()
        if system == "Windows":
            try:
                import windows_toasts  # noqa: F401

                return "windows-toasts"
            except ImportError:
                pass
            try:
                import win10toast  # noqa: F401

                return "win10toast"
            except ImportError:
                return None
        if system == "Darwin":
            return "osascript" if shutil.which("osascript") else None
        if system == "Linux" and shutil.which("notify-send"):
            return "notify-send"
        return None

    def send(self, title: str, message: str) -> None:
        """Fire off a notification; quietly gives up if the backend misbehaves."""
        if not self._backend:
            return
        try:
            getattr(self, f"_send_{self._backend.replace('-', '_')}")(title, message)
        except Exception:
            # A broken notification backend must never interrupt a sync session.
            self._backend = None

    # -- backends --------------------------------------------------------

    def _send_windows_toasts(self, title: str, message: str) -> None:
        from windows_toasts import Toast, WindowsToaster

        toaster = WindowsToaster(APP_NAME)
        toaster.show_toast(Toast(text_fields=[title, message]))

    def _send_win10toast(self, title: str, message: str) -> None:
        from win10toast import ToastNotifier

        ToastNotifier().show_toast(title, message, threaded=True)

    def _send_notify_send(self, title: str, message: str) -> None:
        subprocess.run(
            ["notify-send", "-a", APP_NAME, title, message],
            check=False,
            capture_output=True,
        )

    def _send_osascript(self, title: str, message: str) -> None:
        script = f'display notification {message!r} with title {title!r}'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
