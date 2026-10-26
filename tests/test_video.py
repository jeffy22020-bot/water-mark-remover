import numpy as np

import make_samples
from app.core import video
from app.core.ffmpeg import extract_frame, probe


def test_detects_moving_watermark_and_removes_it(tmp_path):
    src = tmp_path / "clip.mp4"
    truth = make_samples.write_sample_video(src, seconds=4.0)
    info, regions = video.detect_overlays(str(src))
    assert info.width == 640 and info.has_audio
    assert len(regions) >= 2, "watermark that changes corner should give two regions"
    # Each true position is covered by the region active at that time.
    for start, end, mask in truth:
        t = (start + end) / 2
        active = [r for r in regions if r["start"] <= t < r["end"]]
        union = np.zeros_like(mask, bool)
        for r in active:
            union |= r["mask"] > 0
        assert union[mask > 60].mean() > 0.8

    out = tmp_path / "clean.mp4"
    video.remove_watermark(str(src), str(out), [video.Region(r["start"], r["end"], r["mask"]) for r in regions], "fast")
    result = probe(str(out))
    assert result.has_audio
    assert abs(result.duration - info.duration) < 0.25
    before = extract_frame(str(src), 1.0)
    after = extract_frame(str(out), 1.0)
    logo = truth[0][2] > 60
    # The bright watermark text is gone: masked area got much darker / closer to the scene.
    assert after[logo].mean() < before[logo].mean() - 40
