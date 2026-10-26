"""Downloading social-media videos (without the platform watermark when possible).

Uses yt-dlp, which supports TikTok, Instagram, YouTube/Shorts, Facebook, X/Twitter,
Reddit, Pinterest, Vimeo, Snapchat, Douyin, Bilibili and ~1800 other sites. TikTok and
Douyin serve both a watermarked "download" file and the clean playback file; we always
pick a format that isn't marked as watermarked.

Sites change often, so the app can download a newer yt-dlp release into its data folder;
``install_updated_ytdlp`` makes that copy take precedence over the bundled one.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .. import config
from .ffmpeg import ffmpeg_exe
from .inpaint import Cancelled

log = logging.getLogger(__name__)

YTDLP_RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
NO_WM = "[format_note!*=?watermark]"
H264 = "[vcodec~=?'^(avc|h264)']"
FORMAT = "/".join(
    [
        f"bv*{NO_WM}{H264}+ba[ext=m4a]",
        f"bv*{NO_WM}{H264}+ba",
        f"b{NO_WM}{H264}",
        f"bv*{NO_WM}+ba",
        f"b{NO_WM}",
        "bv*+ba",
        "b",
    ]
)
WATERMARKING_SITES = {"tiktok", "douyin"}
URL_RE = re.compile(r"https?://\S+", re.I)


def _ytdlp_zip() -> Path:
    return config.data_dir() / "yt-dlp.zip"


class _ZipFirstFinder:
    """Meta-path finder that loads ``yt_dlp`` from the downloaded release first."""

    def __init__(self, zip_path: str):
        self.zip_path = zip_path

    def find_spec(self, name, path=None, target=None):
        if name != "yt_dlp" and not name.startswith("yt_dlp."):
            return None
        search = path if name != "yt_dlp" else [self.zip_path]
        return importlib.machinery.PathFinder.find_spec(name, search)


def install_updated_ytdlp() -> None:
    """Call before the first ``import yt_dlp``."""
    zip_path = _ytdlp_zip()
    if zip_path.exists() and "yt_dlp" not in sys.modules:
        sys.meta_path.insert(0, _ZipFirstFinder(str(zip_path)))


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def update_ytdlp(progress: Optional[Callable[[float, str], None]] = None) -> str:
    """Download the newest yt-dlp release; it is used after the app restarts."""
    target = _ytdlp_zip()
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(YTDLP_RELEASE, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total, "Downloading the latest downloader…")
    import zipfile

    with zipfile.ZipFile(tmp) as zf:
        version_src = zf.read("yt_dlp/version.py").decode("utf-8", "replace")
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", version_src)
    tmp.replace(target)
    return m.group(1) if m else "latest"


def extract_url(text: str) -> Optional[str]:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,]'\"") if m else None


def _options(extra: Optional[dict] = None) -> dict:
    settings = config.load_settings()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "ffmpeg_location": ffmpeg_exe(),
        "format": FORMAT,
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    browser = settings.get("cookies_browser")
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    if extra:
        opts.update(extra)
    return opts


def _summarize(info: dict) -> dict:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    chosen = info.get("requested_formats") or [info]
    notes = " ".join(str(f.get("format_note") or "") for f in chosen).lower()
    platform_marks = any(site in extractor for site in WATERMARKING_SITES)
    return {
        "title": info.get("title") or info.get("id") or "video",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "platform": info.get("extractor_key") or info.get("extractor") or "web",
        "webpage_url": info.get("webpage_url"),
        "width": info.get("width"),
        "height": info.get("height"),
        # TikTok/Douyin: we picked the clean playback file. Other sites never add a watermark
        # on download, but the video itself may still have one burned in.
        "watermark_free": "watermark" not in notes,
        "platform_watermarks": platform_marks,
    }


def get_info(url: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL(_options({"skip_download": True})) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next(e for e in info["entries"] if e)
    return _summarize(info)


def download(
    url: str,
    out_dir: str,
    progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> tuple[str, dict]:
    import yt_dlp

    def hook(d):
        if cancel and cancel():
            raise Cancelled()
        if d.get("status") == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            speed = d.get("_speed_str") or ""
            progress(min(0.95, got / total) if total else 0.05, f"Downloading… {speed.strip()}")
        elif d.get("status") == "finished" and progress:
            progress(0.96, "Finishing…")

    opts = _options({
        "outtmpl": str(Path(out_dir) / "%(title).80B [%(id)s].%(ext)s"),
        "progress_hooks": [hook],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(e for e in info["entries"] if e)
        path = None
        for item in info.get("requested_downloads") or []:
            path = item.get("filepath") or path
        path = path or ydl.prepare_filename(info)
    if not Path(path).exists():
        mp4 = Path(path).with_suffix(".mp4")
        if mp4.exists():
            path = str(mp4)
    return path, _summarize(info)


def friendly_error(exc: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    text = text.replace("ERROR: ", "")
    lowered = text.lower()
    if "unsupported url" in lowered:
        return "This link isn't a supported video page. Copy the link of the video itself (Share → Copy link)."
    if "login" in lowered or "cookies" in lowered or "private" in lowered:
        return ("This video needs you to be logged in. In Settings, pick the browser you're logged in with "
                "under “Use my browser login”, then try again.")
    if "unable to download" in lowered or "http error" in lowered or "timed out" in lowered:
        return ("The site refused the download. Check your internet connection, or click “Update downloader” "
                "in Settings – sites change often.")
    return text[:400]
