"""Automatic watermark detection.

* Images: text regions found by the PP-OCRv3 DB text detector (several passes so faint,
  semi-transparent text is caught too), plus template matching to find the other copies
  of a repeated (tiled) watermark.
* Videos and image batches: a watermark stays put while the picture underneath changes,
  so pixels whose gradient is the same in most frames are an overlay (median-gradient
  method from Dekel et al., "On the Effectiveness of Visible Watermarks", CVPR 2017).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Iterable, Optional

import cv2
import numpy as np

from .. import config
from .inpaint import Cancelled, _disk

log = logging.getLogger(__name__)

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


class TextDetector:
    def __init__(self, model_path=None):
        self.model_path = model_path or (config.models_dir() / "text_det.onnx")
        self._session = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def _get_session(self):
        if self._session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            self._session = ort.InferenceSession(str(self.model_path), options, providers=["CPUExecutionProvider"])
        return self._session

    def probability(self, image: np.ndarray, side: int = 960) -> np.ndarray:
        """Text probability map (float32, same size as ``image``)."""
        h, w = image.shape[:2]
        ratio = side / max(h, w)
        nh = max(32, int(round(h * ratio / 32)) * 32)
        nw = max(32, int(round(w * ratio / 32)) * 32)
        x = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_AREA if ratio < 1 else cv2.INTER_LINEAR)
        x = (x.astype(np.float32) / 255.0 - MEAN) / STD
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        with self._lock:
            out = self._get_session().run(None, {"x": x})[0]
        prob = np.asarray(out, np.float32).reshape(nh, nw)
        return cv2.resize(prob, (w, h), interpolation=cv2.INTER_LINEAR)


def boxes_from_probability(
    prob: np.ndarray, thresh: float = 0.3, box_thresh: float = 0.5, unclip: float = 2.0, min_size: float = 4
) -> list[np.ndarray]:
    """DB post-processing: probability map -> rotated boxes (4x2 float arrays)."""
    bitmap = (prob > thresh).astype(np.uint8)
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours[:2000]:
        (cx, cy), (bw, bh), angle = cv2.minAreaRect(contour)
        if min(bw, bh) < min_size:
            continue
        x, y, cw, ch = cv2.boundingRect(contour)
        local = np.zeros((ch, cw), np.uint8)
        cv2.fillPoly(local, [contour - [x, y]], 1)
        score = float(prob[y:y + ch, x:x + cw][local > 0].mean()) if local.any() else 0.0
        if score < box_thresh:
            continue
        dist = bw * bh * unclip / (2 * (bw + bh))
        box = cv2.boxPoints(((cx, cy), (bw + 2 * dist, bh + 2 * dist), angle))
        boxes.append(box.astype(np.float32))
    return boxes


def _high_pass(image: np.ndarray) -> np.ndarray:
    """Boost faint marks: subtract the local average and stretch contrast."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sigma = max(4.0, max(image.shape[:2]) / 250.0)
    detail = gray - cv2.GaussianBlur(gray, (0, 0), sigma)
    out = np.clip(128 + detail * 4, 0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def _find_repeats(image: np.ndarray, boxes: list[np.ndarray], limit: int = 12) -> list[np.ndarray]:
    """Template-match detected text to find more copies of a tiled watermark."""
    if len(boxes) < 2:
        return []
    detail = cv2.cvtColor(_high_pass(image), cv2.COLOR_BGR2GRAY)
    h, w = detail.shape
    scale = min(1.0, 1200 / max(h, w))
    small = cv2.resize(detail, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else detail
    found = []
    # Use the most common box size: tiled watermarks are many identical boxes.
    sizes = sorted(boxes, key=lambda b: cv2.contourArea(b), reverse=True)[:limit]
    covered = np.zeros(small.shape, np.uint8)
    for b in boxes:
        cv2.fillPoly(covered, [np.round(b * scale).astype(np.int32)], 1)
    for box in sizes:
        x, y, bw, bh = cv2.boundingRect(np.round(box * scale).astype(np.int32))
        x, y = max(0, x), max(0, y)
        bw, bh = min(bw, small.shape[1] - x), min(bh, small.shape[0] - y)
        if bw < 12 or bh < 8 or bw * bh > small.size * 0.1:
            continue
        template = small[y:y + bh, x:x + bw]
        if template.std() < 8:
            continue
        result = cv2.matchTemplate(small, template, cv2.TM_CCOEFF_NORMED)
        for _ in range(64):
            _, score, _, (px, py) = cv2.minMaxLoc(result)
            if score < 0.35:
                break
            cv2.rectangle(result, (px - bw // 2, py - bh // 2), (px + bw // 2, py + bh // 2), -1, -1)
            if covered[py:py + bh, px:px + bw].mean() > 0.3:
                continue
            covered[py:py + bh, px:px + bw] = 1
            rect = np.array([[px, py], [px + bw, py], [px + bw, py + bh], [px, py + bh]], np.float32) / scale
            found.append(rect)
    return found


_text_detector: Optional[TextDetector] = None


def get_text_detector() -> TextDetector:
    global _text_detector
    if _text_detector is None:
        _text_detector = TextDetector()
    return _text_detector


def detect_text_mask(image: np.ndarray) -> np.ndarray:
    """Mask (uint8 0/255) covering text-like watermarks in a BGR image."""
    detector = get_text_detector()
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    if not detector.available:
        log.warning("Text detector model missing")
        return mask
    prob = detector.probability(image, 960)
    if max(h, w) > 1300:
        prob = np.maximum(prob, detector.probability(image, 1600))
    prob = np.maximum(prob, detector.probability(_high_pass(image), 960))
    boxes = boxes_from_probability(prob)
    boxes += _find_repeats(image, boxes)
    for box in boxes:
        cv2.fillPoly(mask, [np.round(box).astype(np.int32)], 255)
    return mask


# ---------------------------------------------------------------- static overlays


def _gradients(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    g = gray.astype(np.float32)
    return cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)


def static_overlay_mask(frames: list[np.ndarray], min_magnitude: float = 40.0) -> np.ndarray:
    """Mask of structures that stay identical across ``frames`` (grayscale, same size)."""
    if len(frames) < 3:
        return np.zeros(frames[0].shape[:2], np.uint8) if frames else np.zeros((1, 1), np.uint8)
    gx = np.stack([_gradients(f)[0] for f in frames])
    gy = np.stack([_gradients(f)[1] for f in frames])
    mx, my = np.median(gx, axis=0), np.median(gy, axis=0)
    magnitude = np.sqrt(mx * mx + my * my)
    raw = (magnitude > min_magnitude).astype(np.uint8) * 255
    return clean_overlay_mask(raw)


def clean_overlay_mask(raw: np.ndarray) -> np.ndarray:
    h, w = raw.shape
    unit = max(2, int(round(max(h, w) / 160)))
    mask = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, _disk(unit))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    min_area = h * w * 0.00015
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        # Long thin lines (horizon, frame borders) and huge blobs are scenery, not watermarks.
        if (bw > 0.6 * w and bh < 0.06 * h) or (bh > 0.6 * h and bw < 0.06 * w):
            continue
        if (bw / max(bh, 1) > 12 and bh < 0.04 * h) or (bh / max(bw, 1) > 12 and bw < 0.04 * w):
            continue
        if bw * bh > 0.2 * h * w:
            continue
        if area >= min_area or cv2.arcLength(c, True) > unit * 12:
            # Fill letter interiors / logo bodies.
            cv2.drawContours(filled, [c], -1, 255, -1)
    return cv2.dilate(filled, _disk(max(1, unit // 2)))


def detect_batch_overlay(images: list[np.ndarray]) -> Optional[np.ndarray]:
    """Common watermark across several same-sized images (BGR). Returns a full-size mask."""
    if len(images) < 3:
        return None
    h, w = images[0].shape[:2]
    if any(img.shape[:2] != (h, w) for img in images):
        return None
    scale = min(1.0, 640 / max(h, w))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    small = [cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), size, interpolation=cv2.INTER_AREA) for img in images]
    mask = static_overlay_mask(small, min_magnitude=30.0)
    if mask.mean() / 255 > 0.25:
        return None
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)


def detect_video_overlays(
    frames: Iterable[np.ndarray],
    sample_fps: float,
    full_size: tuple[int, int],
    window_sec: float = 3.0,
    total_frames: int = 0,
    progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """Find watermarks in a stream of small grayscale frames sampled at ``sample_fps``.

    Each window of ``window_sec`` seconds gets its own mask so marks that jump between
    corners (TikTok style) are tracked. Returns ``[{start, end, mask}]`` with full-size masks.
    """
    per_window = max(4, int(round(window_sec * sample_fps)))
    windows: list[tuple[float, float, np.ndarray]] = []
    pool: list[np.ndarray] = []  # thin sample of the whole video for the global mask
    current: list[np.ndarray] = []
    index = 0
    start_index = 0
    for frame in frames:
        if cancel and cancel():
            raise Cancelled()
        current.append(frame)
        if index % max(1, per_window // 6) == 0:
            pool.append(frame)
        index += 1
        if len(current) >= per_window:
            windows.append((start_index / sample_fps, index / sample_fps, static_overlay_mask(current)))
            current, start_index = [], index
        if progress and total_frames:
            progress(min(0.95, index / total_frames))
    if len(current) >= 3 or (current and not windows):
        windows.append((start_index / sample_fps, index / sample_fps, static_overlay_mask(current + pool[-3:])))
    elif current and windows:
        s, _, m = windows[-1]
        windows[-1] = (s, index / sample_fps, m)
    if not windows:
        return []

    global_mask = static_overlay_mask(pool) if len(pool) >= 3 else np.zeros_like(windows[0][2])
    if global_mask.mean() / 255 > 0.15:
        # Nothing moves in this video, so "static" can't tell a watermark from the scene.
        global_mask = np.zeros_like(global_mask)

    regions: list[dict] = []
    for start, end, mask in windows:
        if mask.mean() / 255 > 0.15:
            # Mostly-still window (static scene): trust only what is static over the whole video.
            mask = global_mask
        if not mask.any():
            continue
        if regions and regions[-1]["end"] >= start - 1e-6 and _iou(regions[-1]["mask"], mask) > 0.5:
            regions[-1]["end"] = end
            regions[-1]["mask"] = cv2.bitwise_or(regions[-1]["mask"], mask)
            continue
        regions.append({"start": start, "end": end, "mask": mask})

    width, height = full_size
    out = []
    for i, region in enumerate(regions):
        full = cv2.resize(region["mask"], (width, height), interpolation=cv2.INTER_LINEAR)
        full = cv2.dilate(np.where(full > 64, 255, 0).astype(np.uint8), _disk(max(2, width // 400)))
        # Pad region boundaries a little so a mark that moves is never left uncovered.
        start = max(0.0, region["start"] - (0.5 if i else 0))
        end = region["end"] + 0.5 if i < len(regions) - 1 else region["end"] + 3600
        out.append({"start": start, "end": end, "mask": full})
    if progress:
        progress(1.0)
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.count_nonzero((a > 0) & (b > 0))
    union = np.count_nonzero((a > 0) | (b > 0))
    return inter / union if union else 0.0
