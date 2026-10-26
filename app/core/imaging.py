"""Reading and writing still images in every common format."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

try:  # HEIC / HEIF photos from iPhones (and AVIF where supported)
    import pillow_heif

    pillow_heif.register_heif_opener()
    if hasattr(pillow_heif, "register_avif_opener"):
        pillow_heif.register_avif_opener()
except Exception:  # noqa: BLE001 - optional
    pass

Image.MAX_IMAGE_PIXELS = 300_000_000

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif", ".avif", ".ico",
}
# Formats we write back as-is; everything else is saved as PNG (or JPEG for photos).
_WRITABLE = {".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}


@dataclass
class LoadedImage:
    bgr: np.ndarray
    alpha: Optional[np.ndarray] = None
    source_ext: str = ".png"
    icc_profile: Optional[bytes] = None
    exif: Optional[bytes] = None
    dpi: Optional[tuple] = None
    extra: dict = field(default_factory=dict)


def is_animated(path: str) -> bool:
    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:  # noqa: BLE001
        return False


def load(path: str) -> LoadedImage:
    with Image.open(path) as im:
        im.seek(0)
        icc = im.info.get("icc_profile")
        dpi = im.info.get("dpi")
        im = ImageOps.exif_transpose(im)
        exif = None
        try:
            exif_obj = im.getexif()
            if exif_obj:
                exif_obj.pop(0x0112, None)  # orientation is already applied
                exif = exif_obj.tobytes()
        except Exception:  # noqa: BLE001
            exif = None
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if has_alpha:
            rgba = np.asarray(im.convert("RGBA"))
            bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
            alpha = rgba[:, :, 3].copy()
        else:
            rgb = np.asarray(im.convert("RGB"))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            alpha = None
    return LoadedImage(bgr, alpha, Path(path).suffix.lower(), icc, exif, dpi)


def load_bgr(path: str) -> np.ndarray:
    return load(path).bgr


def output_extension(source_ext: str, has_alpha: bool) -> str:
    if source_ext in _WRITABLE:
        return ".jpg" if source_ext in (".jfif",) else source_ext
    return ".png" if has_alpha else ".jpg"


def save(image: LoadedImage, bgr: np.ndarray, out_path: str) -> str:
    ext = Path(out_path).suffix.lower()
    fmt = _WRITABLE.get(ext, "PNG")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if image.alpha is not None and fmt in ("PNG", "WEBP", "TIFF"):
        pil = Image.fromarray(np.dstack([rgb, image.alpha]), "RGBA")
    else:
        pil = Image.fromarray(rgb, "RGB")
    kwargs: dict = {}
    if image.icc_profile:
        kwargs["icc_profile"] = image.icc_profile
    if image.dpi:
        kwargs["dpi"] = image.dpi
    if fmt == "JPEG":
        kwargs.update(quality=95, subsampling=0, optimize=True)
        if image.exif:
            kwargs["exif"] = image.exif
    elif fmt == "WEBP":
        kwargs.update(quality=95, method=4)
        if image.exif:
            kwargs["exif"] = image.exif
    elif fmt == "PNG":
        kwargs.update(optimize=False, compress_level=6)
    elif fmt == "TIFF":
        kwargs.update(compression="tiff_lzw")
    pil.save(out_path, fmt, **kwargs)
    return out_path


def to_preview_bytes(bgr: np.ndarray, alpha: Optional[np.ndarray] = None, max_side: int = 4096) -> tuple[bytes, str]:
    """Browser-friendly preview (PNG when transparent, JPEG otherwise)."""
    h, w = bgr.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1:
        bgr = cv2.resize(bgr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        if alpha is not None:
            alpha = cv2.resize(alpha, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_AREA)
    if alpha is not None:
        ok, buf = cv2.imencode(".png", np.dstack([bgr, alpha]))
        return buf.tobytes(), "image/png"
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes(), "image/jpeg"


def decode_mask(data: bytes, shape: tuple[int, int]) -> np.ndarray:
    """Decode a PNG mask painted in the UI (any size; alpha or brightness = masked)."""
    from .inpaint import normalize_mask

    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError("Invalid mask image")
    return normalize_mask(arr, shape)


def encode_mask_png(mask: np.ndarray) -> bytes:
    """Mask -> RGBA PNG (red, semi-transparent) that the UI overlays directly."""
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    on = mask > 0
    rgba[on] = (80, 80, 255, 255)  # BGRA
    ok, buf = cv2.imencode(".png", rgba)
    return buf.tobytes()


def image_bytes_to_bgr(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        return cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
