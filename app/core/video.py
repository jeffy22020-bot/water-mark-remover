"""Video watermark detection and removal."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from . import detect
from .ffmpeg import FrameReader, FrameWriter, VideoInfo, probe
from .inpaint import Cancelled, _disk, get_inpainter

log = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[float, str], None]]
CancelFn = Optional[Callable[[], bool]]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".flv", ".wmv", ".3gp", ".ts", ".mts", ".gif"}


@dataclass
class Region:
    """A mask that applies between ``start`` and ``end`` seconds."""

    start: float
    end: float
    mask: np.ndarray
    groups: list = field(default_factory=list)


@dataclass
class _Group:
    x0: int
    y0: int
    x1: int
    y1: int
    mask: np.ndarray  # uint8, crop-sized
    paste: np.ndarray  # bool, crop-sized: pixels the inpaint result may change
    ring: np.ndarray  # bool, crop-sized: context just outside ``paste``


def _prepare_groups(mask: np.ndarray) -> list[_Group]:
    h, w = mask.shape
    grouped = cv2.dilate(mask, _disk(24))
    count, _, stats, _ = cv2.connectedComponentsWithStats((grouped > 0).astype(np.uint8))
    groups = []
    for i in range(1, count):
        x, y, bw, bh, _ = stats[i]
        margin = max(64, min(256, max(bw, bh) // 2))
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(w, x + bw + margin), min(h, y + bh + margin)
        crop_mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        # Only this group's own pixels (neighbouring groups get their own crop).
        own = (grouped[y0:y1, x0:x1] > 0)
        crop_mask[own] = mask[y0:y1, x0:x1][own]
        if not crop_mask.any():
            continue
        paste = cv2.dilate(crop_mask, _disk(8)) > 0
        ring = (cv2.dilate(crop_mask, _disk(16)) > 0) & ~paste
        groups.append(_Group(x0, y0, x1, y1, crop_mask, paste, ring))
    return groups


def resolve_quality(mode: str) -> str:
    if mode in ("ai", "fast"):
        return mode
    inpainter = get_inpainter()
    return "ai" if inpainter.ai_available and inpainter.has_gpu() else "fast"


def detect_overlays(path: str, progress: ProgressFn = None, cancel: CancelFn = None) -> tuple[VideoInfo, list[dict]]:
    info = probe(path)
    scale = min(1.0, 480 / max(info.width, info.height))
    sw = max(2, int(round(info.width * scale / 2)) * 2)
    sh = max(2, int(round(info.height * scale / 2)) * 2)
    sample_fps = min(8.0, info.fps)
    total = int(info.duration * sample_fps) + 1
    reader = FrameReader(path, sw, sh, fps=sample_fps, gray=True)

    def report(p: float) -> None:
        if progress:
            progress(p, "Looking for watermarks…")

    # Short clips need short windows, or a mark that moves half-way is never seen on its own.
    window = max(1.0, min(2.5, info.duration / 3))
    regions = detect.detect_video_overlays(
        reader, sample_fps, (info.width, info.height), window_sec=window,
        total_frames=total, progress=report, cancel=cancel,
    )
    return info, regions


def remove_watermark(
    src: str,
    out_path: str,
    regions: list[Region],
    mode: str = "auto",
    crf: int = 18,
    progress: ProgressFn = None,
    cancel: CancelFn = None,
) -> str:
    info = probe(src)
    mode = resolve_quality(mode)
    inpainter = get_inpainter()
    for region in regions:
        region.mask = cv2.resize(region.mask, (info.width, info.height), interpolation=cv2.INTER_NEAREST)
        region.groups = _prepare_groups(region.mask)

    reader = FrameReader(src, info.width, info.height, fps=info.fps)
    writer = FrameWriter(out_path, info.width, info.height, info.fps, src if info.has_audio else None, crf)
    cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
    total = info.frame_count
    started = time.monotonic()
    try:
        for index, frame in enumerate(reader):
            if cancel and cancel():
                raise Cancelled()
            t = index / info.fps
            active = [(ri, r) for ri, r in enumerate(regions) if r.start <= t < r.end]
            if active:
                frame = frame.copy()
                for ri, region in active:
                    for gi, g in enumerate(region.groups):
                        crop = frame[g.y0:g.y1, g.x0:g.x1]
                        ring = crop[g.ring].astype(np.float32)
                        cached = cache.get((ri, gi))
                        diff = float(np.abs(ring - cached[0]).mean()) if cached is not None and ring.size else 1e9
                        if diff < 1.5:
                            # Background around the mark is unchanged: reuse the last fill (no flicker, fast).
                            result = cached[1]
                        else:
                            result = inpainter.inpaint(crop, g.mask, mode, max_tiles=4)
                            if diff < 6:
                                result = cv2.addWeighted(result, 0.5, cached[1], 0.5, 0)
                            cache[(ri, gi)] = (ring, result)
                        crop[g.paste] = result[g.paste]
            writer.write(frame)
            if progress and (index % 5 == 0 or index + 1 == total):
                done = min(1.0, (index + 1) / total)
                elapsed = time.monotonic() - started
                eta = elapsed / done - elapsed if done > 0.01 else 0
                progress(done * 0.99, f"Frame {index + 1} of ~{total} · about {_fmt_eta(eta)} left")
        writer.close()
    except BaseException:
        writer.abort()
        raise
    if progress:
        progress(1.0, "Done")
    return out_path


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
