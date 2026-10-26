"""Background jobs (progress, cancel) and the temporary file store."""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import config
from .core.inpaint import Cancelled

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def update(self, progress: Optional[float] = None, message: Optional[str] = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self.message = message

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
            "message": self.message, "result": self.result, "error": self.error,
            "elapsed": round(time.time() - self.started, 1),
        }


class JobManager:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Job], Any], error_text: Optional[Callable[[Exception], str]] = None) -> Job:
        job = Job(uuid.uuid4().hex[:12], kind)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            job.status = "running"
            try:
                job.result = fn(job)
                job.progress = 1.0
                job.status = "done"
            except Cancelled:
                job.status = "cancelled"
                job.message = "Cancelled"
            except Exception as exc:  # noqa: BLE001 - reported to the UI
                log.error("Job %s failed:\n%s", job.kind, traceback.format_exc())
                job.status = "error"
                job.error = error_text(exc) if error_text else (str(exc) or exc.__class__.__name__)

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job._cancel.set()
            return True
        return False

    def shutdown(self) -> None:
        for job in self._jobs.values():
            job._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class StoredFile:
    id: str
    path: str
    name: str
    kind: str
    parent: Optional[str] = None
    meta: dict = field(default_factory=dict)

    @property
    def mime(self) -> str:
        return mimetypes.guess_type(self.name)[0] or "application/octet-stream"

    def to_dict(self) -> dict:
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        return {"id": self.id, "name": self.name, "kind": self.kind, "size": size, "meta": self.meta, "parent": self.parent}


class FileStore:
    """Files the user opened or produced during this session (in a private temp folder)."""

    def __init__(self):
        root = config.data_dir() / "work"
        root.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale(root)
        self.root = root / f"session-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.root.mkdir()
        self._files: dict[str, StoredFile] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _cleanup_stale(root: Path) -> None:
        cutoff = time.time() - 6 * 3600
        for child in root.glob("session-*"):
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass

    def new_path(self, name: str) -> Path:
        folder = self.root / uuid.uuid4().hex[:10]
        folder.mkdir()
        safe = "".join(ch for ch in name if ch not in '<>:"/\\|?*\0').strip() or "file"
        return folder / safe

    def add(self, path: str, name: str, kind: str, parent: Optional[str] = None, meta: Optional[dict] = None) -> StoredFile:
        stored = StoredFile(uuid.uuid4().hex[:12], str(path), name, kind, parent, meta or {})
        with self._lock:
            self._files[stored.id] = stored
        return stored

    def get(self, file_id: str) -> StoredFile:
        stored = self._files.get(file_id)
        if stored is None or not os.path.exists(stored.path):
            raise KeyError(file_id)
        return stored

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
