import cv2
import numpy as np

import make_samples
from app.core import detect
from conftest import needs_models


@needs_models()
def test_text_watermark_detected():
    _, marked, truth = make_samples.sample_tiled_image()
    mask = detect.detect_text_mask(marked)
    truth = truth > 40
    assert (mask[truth] > 0).mean() > 0.6


@needs_models()
def test_clean_image_has_few_detections():
    clean = make_samples.landscape(1000, 700, seed=21)
    assert (detect.detect_text_mask(clean) > 0).mean() < 0.01


def test_static_overlay_found_across_frames():
    rng = np.random.default_rng(1)
    logo = np.zeros((240, 320), np.uint8)
    cv2.putText(logo, "LOGO", (200, 220), cv2.FONT_HERSHEY_DUPLEX, 1.0, 255, 2)
    frames = []
    for i in range(12):
        frame = cv2.GaussianBlur(rng.integers(0, 255, (240, 320), dtype=np.uint8), (0, 0), 3)
        frame = np.where(logo > 0, 240, frame).astype(np.uint8)
        frames.append(frame)
    mask = detect.static_overlay_mask(frames)
    assert (mask[logo > 0] > 0).mean() > 0.8
    assert (mask > 0).mean() < 0.1


def test_batch_overlay_requires_same_size():
    imgs = [np.zeros((100, 100, 3), np.uint8), np.zeros((100, 120, 3), np.uint8), np.zeros((100, 100, 3), np.uint8)]
    assert detect.detect_batch_overlay(imgs) is None
