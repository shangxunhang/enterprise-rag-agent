"""Strict lifecycle wrapper for one private Microsoft Word COM instance."""

from __future__ import annotations

import ctypes
import gc
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .exceptions import WordUnavailableError

WD_DO_NOT_SAVE_CHANGES = 0
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
WM_CLOSE = 0x0010


class WordApplication:
    """Create one non-interactive Word instance and always release it."""

    def __init__(self, *, dispatch_factory: Any | None = None) -> None:
        self._dispatch_factory = dispatch_factory
        self._pythoncom: Any | None = None
        self.application: Any | None = None
        self.owned_process_id: int | None = None
        self.owned_window_handle: int | None = None
        self.process_exited: bool | None = None

    def __enter__(self) -> "WordApplication":
        try:
            if self._dispatch_factory is None:
                import pythoncom  # type: ignore[import-not-found]
                from win32com.client import DispatchEx  # type: ignore[import-not-found]

                self._pythoncom = pythoncom
                pythoncom.CoInitialize()
                factory = DispatchEx
            else:
                factory = self._dispatch_factory
            app = factory("Word.Application")
            self.application = app
            app.Visible = False
            app.DisplayAlerts = 0
            try:
                app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
            except Exception:
                pass
            try:
                app.Options.UpdateLinksAtOpen = False
            except Exception:
                pass
            self.owned_window_handle = _hwnd_from_word_window(app)
            self.owned_process_id = _pid_from_hwnd(self.owned_window_handle)
            return self
        except Exception as exc:
            self._release_com()
            raise WordUnavailableError(f"cannot start Microsoft Word COM: {exc}") from exc

    @contextmanager
    def open_document(self, path: Path) -> Iterator[Any]:
        if self.application is None:
            raise RuntimeError("WordApplication is not active")
        document = None
        try:
            document = self.application.Documents.Open(
                FileName=str(path.resolve(strict=True)),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Revert=False,
                NoEncodingDialog=True,
            )
            self.capture_document_window(document)
            yield document
        finally:
            if document is not None:
                try:
                    document.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
                finally:
                    del document

    def capture_document_window(self, document: Any) -> None:
        """Record the HWND/PID of a document created or opened by this instance."""

        if self.application is None:
            raise RuntimeError("WordApplication is not active")
        hwnd = _hwnd_from_document_window(document, self.application)
        if hwnd is not None:
            self.owned_window_handle = hwnd
            self.owned_process_id = _pid_from_hwnd(hwnd)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._release_com()

    def _release_com(self) -> None:
        app = self.application
        self.application = None
        if app is not None:
            hwnd = self.owned_window_handle or _hwnd_from_word_window(app)
            if hwnd is not None:
                self.owned_window_handle = hwnd
                if self.owned_process_id is None:
                    self.owned_process_id = _pid_from_hwnd(hwnd)
                # Release the COM proxy while the private Word server is alive,
                # then send the same graceful close request as the Word window.
                # This avoids a Word/pywin32 first-chance RPC exception raised
                # inside Application.Quit on some Windows Office builds.
                del app
                gc.collect()
                _post_window_close(hwnd)
            else:
                try:
                    app.Quit(WD_DO_NOT_SAVE_CHANGES)
                except Exception:
                    pass
                del app
                gc.collect()
        if self.owned_process_id is not None:
            self.process_exited = _wait_for_process_exit(self.owned_process_id)
        if self._pythoncom is not None:
            try:
                self._pythoncom.CoUninitialize()
            finally:
                self._pythoncom = None


def _hwnd_from_word_window(application: Any) -> int | None:
    for owner in (application, _safe_com_attribute(application, "ActiveWindow")):
        hwnd = _safe_com_attribute(owner, "Hwnd")
        try:
            value = int(hwnd)
            if value > 0:
                return value
        except Exception:
            pass
    return None


def _hwnd_from_document_window(document: Any, application: Any) -> int | None:
    for owner in (
        _safe_com_attribute(document, "ActiveWindow"),
        _safe_com_attribute(application, "ActiveWindow"),
        application,
    ):
        hwnd = _safe_com_attribute(owner, "Hwnd")
        try:
            value = int(hwnd)
            if value > 0:
                return value
        except Exception:
            pass
    return None


def _pid_from_hwnd(hwnd: Any) -> int | None:
    try:
        hwnd = int(hwnd)
        if hwnd <= 0:
            return None
        try:
            import win32process  # type: ignore[import-not-found]

            return int(win32process.GetWindowThreadProcessId(hwnd)[1]) or None
        except Exception:
            pass
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        return None


def _safe_com_attribute(value: Any, name: str) -> Any | None:
    if value is None:
        return None
    try:
        return getattr(value, name)
    except Exception:
        return None


def _post_window_close(hwnd: int) -> bool:
    try:
        return bool(ctypes.windll.user32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0))
    except Exception:
        return False


def _process_exists(pid: int) -> bool:
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _wait_for_process_exit(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.1)
    return not _process_exists(pid)
