import numpy as np

import make_samples
from app.core.inpaint import Inpainter, normalize_mask, plan_tiles
from conftest import needs_models


def _case(size=(360, 480)):
    clean = make_samples.landscape(size[1], size[0], seed=7)
    mask = np.zeros(size, np.uint8)
    mask[150:200, 120:330] = 255
    marked = clean.copy()
    marked[mask > 0] = (marked[mask > 0] * 0.3 + 255 * 0.7).astype(np.uint8)
    return clean, marked, mask


def _err(a, b, mask):
    return float(np.abs(a.astype(int) - b.astype(int))[mask > 0].mean())


def test_fast_mode_restores_masked_area():
    clean, marked, mask = _case()
    out = Inpainter().inpaint(marked, mask, "fast")
    assert out.shape == clean.shape
    assert _err(out, clean, mask) < _err(marked, clean, mask) / 3
    # Pixels far from the mask are untouched.
    far = np.ones_like(mask)
    far[130:220, 100:350] = 0
    assert _err(out, marked, far) == 0


@needs_models()
def test_ai_mode_restores_masked_area():
    clean, marked, mask = _case()
    out = Inpainter().inpaint(marked, mask, "ai")
    assert _err(out, clean, mask) < _err(marked, clean, mask) / 4


def test_empty_mask_returns_copy():
    clean, _, _ = _case()
    out = Inpainter().inpaint(clean, np.zeros(clean.shape[:2], np.uint8), "ai")
    assert np.array_equal(out, clean) and out is not clean


def test_tiles_cover_every_masked_pixel():
    mask = np.zeros((1500, 2200), np.uint8)
    mask[100:120, 50:2100] = 255
    mask[1400:1450, 1900:2150] = 255
    covered = np.zeros_like(mask)
    for t in plan_tiles(mask):
        assert t.w == 512 and t.h == 512
        covered[t.y:t.y + t.h, t.x:t.x + t.w] = 1
    assert covered[mask > 0].all()


def test_normalize_mask_accepts_rgba_and_resizes():
    rgba = np.zeros((50, 80, 4), np.uint8)
    rgba[10:20, 10:20, 3] = 255
    out = normalize_mask(rgba, (100, 160))
    assert out.shape == (100, 160) and out.max() == 255 and out[30, 30] == 255
