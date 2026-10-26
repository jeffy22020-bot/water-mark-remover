"""Thin helpers around the ffmpeg binary bundled by imageio-ffmpeg."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Iterator, Optional

import cv2
import numpy as np


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    override = os.environ.get("WMR_FFMPEG")
    if override:
        return override
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def popen_kwargs() -> dict:
    """Keep ffmpeg from flashing a console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run(args: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_exe(), "-hide_banner", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        **popen_kwargs(),
    )


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    codec: str

    @property
    def frame_count(self) -> int:
        return max(1, int(round(self.duration * self.fps)))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["frame_count"] = self.frame_count
        return data


def extract_frame(path: str, t: float = 0.0, max_side: Optional[int] = None) -> Optional[np.ndarray]:
    """Decode one frame (BGR) at time ``t`` seconds."""
    args = []
    if t > 0:
        args += ["-ss", f"{t:.3f}"]
    args += ["-i", path, "-frames:v", "1", "-an", "-sn"]
    if max_side:
        args += ["-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"]
    args += ["-f", "image2pipe", "-c:v", "png", "-"]
    proc = run(args, timeout=60)
    if proc.returncode != 0 or not proc.stdout:
        if t > 0:
            # Seeking past the last frame yields nothing; fall back to a slightly earlier time.
            return extract_frame(path, max(0.0, t - 0.5), max_side) if t > 0.5 else extract_frame(path, 0, max_side)
        return None
    return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)


def probe(path: str) -> VideoInfo:
    proc = run(["-i", path], timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    video_line = next((line for line in text.splitlines() if re.search(r"Stream #.*Video:", line)), None)
    if video_line is None:
        raise ValueError("This file does not contain a video stream.")
    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    fps = 0.0
    for pattern in (r"([\d.]+)\s*fps", r"([\d.]+)\s*tbr"):
        m = re.search(pattern, video_line)
        if m:
            fps = float(m.group(1))
            if 0 < fps <= 240:
                break
    if not 0 < fps <= 240:
        fps = 30.0
    codec_match = re.search(r"Video:\s*([\w-]+)", video_line)
    has_audio = bool(re.search(r"Stream #.*Audio:", text))
    frame = extract_frame(path, 0)
    if frame is None:
        raise ValueError("Could not decode this video.")
    height, width = frame.shape[:2]
    if duration <= 0:
        duration = 1.0 / fps
    return VideoInfo(width, height, fps, duration, has_audio, codec_match.group(1) if codec_match else "?")


class FrameReader:
    """Iterate decoded frames as numpy arrays through an ffmpeg pipe."""

    def __init__(
        self,
        path: str,
        width: int,
        height: int,
        fps: Optional[float] = None,
        gray: bool = False,
    ):
        self.width, self.height, self.gray = width, height, gray
        filters = []
        if fps:
            filters.append(f"fps={fps:.6f}")
        filters.append(f"scale={width}:{height}:flags=area")
        self._log = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [
                ffmpeg_exe(), "-hide_banner", "-v", "error", "-i", path, "-an", "-sn",
                "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "gray" if gray else "bgr24", "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=self._log,
            bufsize=10**7,
            **popen_kwargs(),
        )

    def __iter__(self) -> Iterator[np.ndarray]:
        channels = 1 if self.gray else 3
        size = self.width * self.height * channels
        shape = (self.height, self.width) if self.gray else (self.height, self.width, 3)
        try:
            while True:
                buf = self.proc.stdout.read(size)
                if len(buf) < size:
                    break
                yield np.frombuffer(buf, np.uint8).reshape(shape)
        finally:
            self.close()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        if self.proc.stdout:
            self.proc.stdout.close()
        self._log.close()


class FrameWriter:
    """Encode BGR frames to a video file, muxing audio from ``audio_source``."""

    def __init__(
        self,
        out_path: str,
        width: int,
        height: int,
        fps: float,
        audio_source: Optional[str] = None,
        crf: int = 18,
    ):
        self.out_path = out_path
        self._log = tempfile.TemporaryFile()
        is_gif = out_path.lower().endswith(".gif")
        cmd = [
            ffmpeg_exe(), "-hide_banner", "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
        ]
        if is_gif:
            cmd += ["-vf", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer", "-loop", "0"]
        else:
            if audio_source:
                cmd += ["-i", audio_source, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k"]
            cmd += [
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(int(crf)),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            ]
        cmd.append(out_path)
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log, **popen_kwargs()
        )

    def write(self, frame: np.ndarray) -> None:
        try:
            self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError as exc:
            raise RuntimeError(f"Video encoder stopped: {self._error()}") from exc

    def _error(self) -> str:
        self._log.seek(0)
        return self._log.read().decode("utf-8", "replace").strip()[-800:]

    def close(self) -> None:
        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        code = self.proc.wait()
        error = self._error()
        self._log.close()
        if code != 0:
            raise RuntimeError(f"Video encoding failed: {error}")

    def abort(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self._log.close()


def make_preview(path: str, out_path: str, max_side: int = 960) -> None:
    """Transcode to a small H.264 file the built-in browser can always play."""
    proc = run(
        [
            "-y", "-i", path,
            "-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease,"
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path,
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-500:])
