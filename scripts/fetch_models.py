"""Download the AI models into ./models (verified by SHA-256).

    python scripts/fetch_models.py

Both models come from the OpenCV model zoo (Apache-2.0):
* LaMa inpainting  – fills in the area under a watermark
* PP-OCRv3 DB text detector – finds text watermarks automatically
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = {
    "lama.onnx": (
        "7df918ac3921d3daf0aae1d219776cf0dc4e4935f035af81841b40adcf74fdf2",
        [
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/inpainting_lama/inpainting_lama_2025jan.onnx",
            "https://huggingface.co/opencv/inpainting_lama/resolve/main/inpainting_lama_2025jan.onnx",
        ],
    ),
    "text_det.onnx": (
        "03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587",
        [
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/text_detection_ppocr/text_detection_en_ppocrv3_2023may.onnx",
            "https://huggingface.co/opencv/text_detection_ppocr/resolve/main/text_detection_en_ppocrv3_2023may.onnx",
        ],
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name: str, digest: str, urls: list[str], out_dir: Path) -> None:
    target = out_dir / name
    if target.exists() and sha256(target) == digest:
        print(f"{name}: ok")
        return
    for url in urls:
        for attempt in range(3):
            try:
                print(f"{name}: downloading {url}")
                tmp = target.with_suffix(".part")
                with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                if sha256(tmp) != digest:
                    tmp.unlink()
                    raise ValueError("checksum mismatch")
                tmp.replace(target)
                print(f"{name}: ok")
                return
            except Exception as exc:  # noqa: BLE001 - retry, then try the mirror
                print(f"  failed ({exc})")
                time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Could not download {name}")


def main() -> None:
    out_dir = ROOT / "models"
    out_dir.mkdir(exist_ok=True)
    for name, (digest, urls) in MODELS.items():
        fetch(name, digest, urls, out_dir)


if __name__ == "__main__":
    main()
    sys.exit(0)
