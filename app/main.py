"""Start the Watermark Remover desktop app.

    python -m app.main              # desktop window (Edge WebView2 on Windows)
    python -m app.main --browser    # open in the default web browser instead
    python -m app.main --self-test  # check that everything works, then exit
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# A windowed (no console) Windows build has no stdout/stderr; some libraries write to them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115

from app import APP_NAME, __version__, config  # noqa: E402

log = logging.getLogger("app")

FILE_TYPES = {
    "image": ("Images (*.jpg;*.jpeg;*.jfif;*.png;*.webp;*.bmp;*.tif;*.tiff;*.gif;*.heic;*.heif;*.avif)",),
    "video": ("Videos (*.mp4;*.mov;*.m4v;*.webm;*.mkv;*.avi;*.mpg;*.mpeg;*.flv;*.wmv;*.3gp;*.ts;*.mts;*.gif)",),
    "pdf": ("PDF files (*.pdf)",),
    "office": ("PowerPoint and Word (*.pptx;*.pptm;*.ppsx;*.potx;*.docx;*.docm;*.dotx;*.ppt;*.pps;*.odp;*.doc;*.odt;*.rtf)",),
}


def setup_logging() -> Path:
    log_path = config.data_dir() / "app.log"
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if not config.is_frozen():
        root.addHandler(logging.StreamHandler(sys.stderr))
    return log_path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """Functions the web UI can call in the desktop window (``window.pywebview.api``)."""

    def __init__(self, store):
        self._store = store
        self._window = None

    def _dialog(self, kind_name: str):
        import webview

        enum = getattr(webview, "FileDialog", None)
        if enum is not None:
            return getattr(enum, kind_name)
        return getattr(webview, f"{kind_name}_DIALOG")

    def pick_files(self, kind: str, multiple: bool = False):
        types = FILE_TYPES.get(kind, ()) + ("All files (*.*)",)
        result = self._window.create_file_dialog(self._dialog("OPEN"), allow_multiple=bool(multiple), file_types=types)
        return list(result or [])

    def pick_folder(self):
        result = self._window.create_file_dialog(self._dialog("FOLDER"))
        return result[0] if result else None

    def save_file(self, file_id: str, suggested_name: str):
        stored = self._store.get(file_id)
        folder = config.load_settings().get("output_dir") or str(config.default_output_dir())
        ext = Path(stored.name).suffix
        types = (f"{ext.upper().lstrip('.')} file (*{ext})", "All files (*.*)")
        result = self._window.create_file_dialog(
            self._dialog("SAVE"), directory=folder, save_filename=suggested_name or stored.name, file_types=types
        )
        if not result:
            return None
        target = result if isinstance(result, str) else result[0]
        if ext and not target.lower().endswith(ext.lower()):
            target += ext
        shutil.copyfile(stored.path, target)
        return target

    def read_clipboard(self):
        """Clipboard text (WebView2 may block navigator.clipboard on plain http pages)."""
        if sys.platform != "win32":
            return ""
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(pointer) if pointer else ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def save_many(self, file_ids: list):
        folder = self.pick_folder()
        if not folder:
            return None
        for fid in file_ids:
            stored = self._store.get(fid)
            target = Path(folder) / stored.name
            n = 2
            while target.exists():
                target = Path(folder) / f"{Path(stored.name).stem} ({n}){Path(stored.name).suffix}"
                n += 1
            shutil.copyfile(stored.path, target)
        return folder


def start_server(desktop: bool):
    import uvicorn

    from app.jobs import FileStore, JobManager
    from app.server import create_app

    token = secrets.token_urlsafe(24)
    store = FileStore()
    jobs = JobManager()
    application = create_app(token, store, jobs, desktop=desktop)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning", access_log=False, log_config=None)
    )
    thread = threading.Thread(target=server.run, name="server", daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline and thread.is_alive():
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("The app engine could not start.")
    return server, store, jobs, f"http://127.0.0.1:{port}/?t={token}"


def run_browser(url: str) -> None:
    print(f"{APP_NAME} is running at:\n  {url}\nPress Ctrl+C to quit.", flush=True)
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_window(url: str, store) -> bool:
    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        log.warning("pywebview unavailable (%s); opening the browser instead", exc)
        return False
    bridge = Bridge(store)
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:  # noqa: BLE001 - older pywebview
        pass
    window = webview.create_window(
        APP_NAME, url, js_api=bridge, width=1320, height=880, min_size=(980, 660), background_color="#111214",
    )
    bridge._window = window
    try:
        webview.start(debug=bool(os.environ.get("WMR_DEBUG")), private_mode=False, storage_path=str(config.data_dir() / "webview"))
    except Exception as exc:  # noqa: BLE001 - e.g. WebView2 runtime missing
        log.error("Could not open the app window: %s", exc)
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="WatermarkRemover", description=APP_NAME)
    parser.add_argument("--browser", action="store_true", help="open in the default web browser")
    parser.add_argument("--self-test", action="store_true", help="run a quick end-to-end check and exit")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    setup_logging()
    log.info("%s %s starting (frozen=%s)", APP_NAME, __version__, config.is_frozen())

    from app.core import social

    social.install_updated_ytdlp()

    if args.self_test:
        from app.selftest import run_self_test

        return run_self_test()

    server, store, jobs, url = start_server(desktop=not args.browser)
    try:
        if args.browser or not run_window(url, store):
            run_browser(url)
    finally:
        server.should_exit = True
        jobs.shutdown()
        store.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
