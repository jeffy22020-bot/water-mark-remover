"""Fill masked areas of an image.

Two engines are available:

* ``ai``   – the LaMa inpainting network (ONNX, runs on GPU through DirectML or on CPU).
* ``fast`` – OpenCV's Telea algorithm; instant, good for small marks on simple backgrounds.

The LaMa network has a fixed 512x512 input, so large images are processed in
overlapping 512px tiles placed only where the mask is, and big holes are handled
at a reduced scale so the network always sees enough surrounding context.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional

import cv2
import numpy as np

from .. import config

log = logging.getLogger(__name__)

LAMA_SIZE = 512
# Holes thicker than this (radius in px, at working scale) are processed at a reduced scale.
MAX_HOLE_RADIUS = 96
TILE_OVERLAP = 128
# Minimum context kept around a group of masked pixels inside a tile.
CONTEXT_MARGIN = 64

ProgressFn = Optional[Callable[[float], None]]
CancelFn = Optional[Callable[[], bool]]


class Cancelled(Exception):
    """Raised when the user cancels a running job."""


@dataclass
class Tile:
    x: int
    y: int
    w: int
    h: int


def _check_cancel(cancel: CancelFn) -> None:
    if cancel is not None and cancel():
        raise Cancelled()


def _disk(radius: int) -> np.ndarray:
    radius = max(1, int(radius))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def normalize_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Return a uint8 0/255 single-channel mask matching ``shape`` (h, w)."""
    if mask.ndim == 3:
        if mask.shape[2] == 4:
            # Painted masks come from the UI as RGBA with transparent background.
            mask = mask[:, :, 3]
        else:
            mask = mask.max(axis=2)
    if mask.shape[:2] != shape:
        mask = cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return np.where(mask > 127, 255, 0).astype(np.uint8)


def plan_tiles(mask: np.ndarray, tile: int = LAMA_SIZE) -> list[Tile]:
    """Place ``tile``-sized squares so that every masked pixel is covered."""
    h, w = mask.shape
    tw, th = min(tile, w), min(tile, h)
    grouped = cv2.dilate(mask, _disk(CONTEXT_MARGIN // 2))
    count, _, stats, _ = cv2.connectedComponentsWithStats((grouped > 0).astype(np.uint8), connectivity=8)
    tiles: list[Tile] = []
    seen: set[tuple[int, int]] = set()
    stride = tile - TILE_OVERLAP

    def add(x: int, y: int) -> None:
        x = int(min(max(0, x), w - tw))
        y = int(min(max(0, y), h - th))
        if (x, y) in seen:
            return
        if not mask[y:y + th, x:x + tw].any():
            return
        seen.add((x, y))
        tiles.append(Tile(x, y, tw, th))

    for i in range(1, count):
        gx, gy, gw, gh, _ = stats[i]
        if gw <= tw - 2 * CONTEXT_MARGIN // 2 and gh <= th - 2 * CONTEXT_MARGIN // 2:
            add(gx + gw // 2 - tw // 2, gy + gh // 2 - th // 2)
            continue
        xs = list(range(gx - CONTEXT_MARGIN // 2, gx + gw, stride)) or [gx]
        ys = list(range(gy - CONTEXT_MARGIN // 2, gy + gh, stride)) or [gy]
        for ty in ys:
            for tx in xs:
                add(tx, ty)
    return tiles


@lru_cache(maxsize=1)
def hardware_gpus() -> tuple[str, ...]:
    """Names of real display adapters (Windows). DirectML on a software/virtual adapter is slower than the CPU."""
    if sys.platform != "win32":
        return ()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except Exception:  # noqa: BLE001 - can't tell; let DirectML decide
        return ("Graphics card",)
    names = [n.strip() for n in out.splitlines() if n.strip()]
    virtual = re.compile(r"basic (render|display)|remote|hyper-v|virtual|vmware|virtualbox|parsec|citrix|indirect|idd", re.I)
    return tuple(n for n in names if not virtual.search(n))


def _tile_weight(th: int, tw: int, ramp: int = TILE_OVERLAP // 2) -> np.ndarray:
    ys = np.minimum(np.arange(th), np.arange(th)[::-1]) + 1
    xs = np.minimum(np.arange(tw), np.arange(tw)[::-1]) + 1
    wy = np.clip(ys / ramp, 0.02, 1.0)
    wx = np.clip(xs / ramp, 0.02, 1.0)
    return (wy[:, None] * wx[None, :]).astype(np.float32)


class Inpainter:
    def __init__(self, model_path=None):
        self.model_path = model_path or (config.models_dir() / "lama.onnx")
        self._session = None
        self._provider = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ session
    @property
    def ai_available(self) -> bool:
        return self.model_path.exists()

    @staticmethod
    def gpu_providers() -> list[str]:
        try:
            import onnxruntime as ort
        except ImportError:
            return []
        wanted = ("DmlExecutionProvider", "CUDAExecutionProvider", "CoreMLExecutionProvider")
        available = ort.get_available_providers()
        found = [p for p in wanted if p in available]
        if found == ["DmlExecutionProvider"] and not hardware_gpus():
            return []
        return found

    def has_gpu(self) -> bool:
        return bool(self.gpu_providers()) and config.load_settings().get("device") != "cpu"

    def device_name(self) -> str:
        provider = self._provider or (self.gpu_providers()[:1] or ["CPUExecutionProvider"])[0]
        if config.load_settings().get("device") == "cpu":
            provider = "CPUExecutionProvider"
        if provider == "DmlExecutionProvider" and hardware_gpus():
            return f"GPU ({hardware_gpus()[0]})"
        return {
            "DmlExecutionProvider": "GPU (DirectML)",
            "CUDAExecutionProvider": "GPU (CUDA)",
            "CoreMLExecutionProvider": "GPU (CoreML)",
        }.get(provider, "CPU")

    def reset(self) -> None:
        with self._lock:
            self._session = None
            self._provider = None

    def _create_session(self, providers: list[str]):
        import onnxruntime as ort

        ort.disable_telemetry_events()

        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if "DmlExecutionProvider" in providers:
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return ort.InferenceSession(str(self.model_path), options, providers=providers)

    def _get_session(self):
        if self._session is not None:
            return self._session
        if not self.ai_available:
            raise RuntimeError("The AI model is missing. Reinstall the app or use Fast mode.")
        candidates = []
        if config.load_settings().get("device") != "cpu":
            candidates += [[p, "CPUExecutionProvider"] for p in self.gpu_providers()]
        candidates.append(["CPUExecutionProvider"])
        last_error = None
        for providers in candidates:
            try:
                self._session = self._create_session(providers)
                self._provider = self._session.get_providers()[0]
                log.info("LaMa session ready on %s", self._provider)
                return self._session
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = exc
                log.warning("Could not start LaMa on %s: %s", providers[0], exc)
        raise RuntimeError(f"Could not load the AI model: {last_error}")

    def _run_lama(self, crop: np.ndarray, crop_mask: np.ndarray) -> np.ndarray:
        """Run LaMa on a BGR crop up to 512x512; returns float32 BGR of the same size."""
        h, w = crop_mask.shape
        pad_y, pad_x = LAMA_SIZE - h, LAMA_SIZE - w
        if pad_y or pad_x:
            # Smaller crops are mirrored out to the network's fixed input size (no rescaling).
            border = cv2.BORDER_REFLECT_101 if min(h, w) > 1 else cv2.BORDER_REPLICATE
            crop = cv2.copyMakeBorder(crop, 0, pad_y, 0, pad_x, border)
            crop_mask = cv2.copyMakeBorder(crop_mask, 0, pad_y, 0, pad_x, cv2.BORDER_CONSTANT, value=0)
        m = (crop_mask > 0).astype(np.float32)
        rgb = crop[:, :, ::-1].astype(np.float32) / 255.0
        image = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]) * (1.0 - m)[None, None]
        feed = {"image": image.astype(np.float32), "mask": m[None, None]}
        with self._lock:
            session = self._get_session()
            try:
                out = session.run(None, feed)[0][0]
            except Exception as exc:  # noqa: BLE001 - GPU drivers can fail at run time
                if self._provider == "CPUExecutionProvider":
                    raise
                log.warning("GPU inference failed (%s); falling back to CPU", exc)
                self._session = self._create_session(["CPUExecutionProvider"])
                self._provider = "CPUExecutionProvider"
                out = self._session.run(None, feed)[0][0]
        out = np.clip(out.transpose(1, 2, 0)[:, :, ::-1], 0, 255).astype(np.float32)
        return out[:h, :w]

    # ---------------------------------------------------------------- inpaint
    def inpaint(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        mode: str = "ai",
        progress: ProgressFn = None,
        cancel: CancelFn = None,
        max_tiles: Optional[int] = None,
    ) -> np.ndarray:
        """Return ``image`` (BGR uint8) with the masked pixels filled in."""
        if max_tiles is None:
            # A CPU is roughly 10x slower than a GPU, so it works at a coarser scale on huge masks.
            max_tiles = 64 if self.has_gpu() else 16
        mask = normalize_mask(mask, image.shape[:2])
        if not mask.any():
            return image.copy()
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if mode == "ai" and not self.ai_available:
            log.warning("AI model missing; using fast mode")
            mode = "fast"
        if mode == "ai":
            filled, grow = self._fill_ai(image, mask, progress, cancel, max_tiles)
        else:
            filled, grow = self._fill_fast(image, mask, progress, cancel)
        return self._composite(image, mask, filled, grow)

    @staticmethod
    def _composite(image: np.ndarray, mask: np.ndarray, filled: np.ndarray, grow: int) -> np.ndarray:
        grown = cv2.dilate(mask, _disk(grow))
        alpha = cv2.GaussianBlur(grown.astype(np.float32) / 255.0, (0, 0), max(0.8, grow / 2.0))
        alpha = np.maximum(alpha, mask.astype(np.float32) / 255.0)[:, :, None]
        out = image.astype(np.float32) * (1.0 - alpha) + filled.astype(np.float32) * alpha
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)

    def _working_scale(self, mask: np.ndarray, max_tiles: int) -> float:
        dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
        radius = float(dist.max())
        scale = min(1.0, MAX_HOLE_RADIUS / radius) if radius > 0 else 1.0
        while scale > 0.15:
            small = self._resize_mask(mask, scale)
            if len(plan_tiles(small)) <= max_tiles:
                break
            scale *= 0.8
        return scale

    @staticmethod
    def _resize_mask(mask: np.ndarray, scale: float) -> np.ndarray:
        if scale == 1.0:
            return mask
        h, w = mask.shape
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        small = cv2.resize(mask, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST)
        return np.where(small > 0, 255, 0).astype(np.uint8)

    def _fill_ai(self, image, mask, progress, cancel, max_tiles):
        h, w = mask.shape
        scale = self._working_scale(mask, max_tiles)
        grow = max(2, int(round(2 / min(scale, 1.0))))
        if scale != 1.0:
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            work = cv2.resize(image, size, interpolation=interp)
        else:
            work = image
        work_mask = self._resize_mask(mask, scale)
        # Grow the hole a little: removes anti-aliased edges and halos around the mark.
        work_mask = cv2.dilate(work_mask, _disk(int(np.ceil(2 * grow * scale)) + 1))
        tiles = plan_tiles(work_mask)
        acc = np.zeros(work.shape, np.float32)
        weight = np.zeros(work_mask.shape, np.float32)
        for i, t in enumerate(tiles):
            _check_cancel(cancel)
            crop = work[t.y:t.y + t.h, t.x:t.x + t.w]
            crop_mask = work_mask[t.y:t.y + t.h, t.x:t.x + t.w]
            out = self._run_lama(crop, crop_mask)
            wgt = _tile_weight(t.h, t.w)
            acc[t.y:t.y + t.h, t.x:t.x + t.w] += out * wgt[:, :, None]
            weight[t.y:t.y + t.h, t.x:t.x + t.w] += wgt
            if progress:
                progress((i + 1) / len(tiles))
        filled = work.astype(np.float32)
        covered = weight > 0
        filled[covered] = acc[covered] / weight[covered][:, None]
        if scale != 1.0:
            filled = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC)
            # Keep full-resolution detail outside the (upscaled) hole.
            hole = cv2.resize(work_mask, (w, h), interpolation=cv2.INTER_NEAREST) > 0
            filled = np.where(hole[:, :, None], filled, image.astype(np.float32))
        return filled, grow

    def _fill_fast(self, image, mask, progress, cancel):
        grow = 2
        grown = cv2.dilate(mask, _disk(grow + 1))
        filled = image.copy()
        count, _, stats, _ = cv2.connectedComponentsWithStats((cv2.dilate(grown, _disk(8)) > 0).astype(np.uint8))
        h, w = mask.shape
        for i in range(1, count):
            _check_cancel(cancel)
            x, y, bw, bh, _ = stats[i]
            pad = max(16, min(bw, bh) // 2)
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
            crop = filled[y0:y1, x0:x1]
            filled[y0:y1, x0:x1] = cv2.inpaint(crop, grown[y0:y1, x0:x1], 5, cv2.INPAINT_TELEA)
            if progress:
                progress(i / max(1, count - 1))
        return filled, grow


_instance: Optional[Inpainter] = None
_instance_lock = threading.Lock()


def get_inpainter() -> Inpainter:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Inpainter()
        return _instance
