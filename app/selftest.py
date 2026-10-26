"""End-to-end smoke test (``WatermarkRemover.exe --self-test``), used by CI on the built app."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

from . import config


def _check(name, fn, results):
    started = time.time()
    try:
        detail = fn() or ""
        results.append((True, name, detail, time.time() - started))
    except Exception as exc:  # noqa: BLE001 - report every failure
        results.append((False, name, f"{exc}\n{traceback.format_exc()}", time.time() - started))


def run_self_test() -> int:
    import numpy as np

    from .core import detect, imaging, office, pdf, social, video
    from .core.ffmpeg import ffmpeg_exe, probe, run
    from .core.inpaint import get_inpainter

    samples = config.samples_dir()
    tmp = Path(tempfile.mkdtemp(prefix="wmr-selftest-"))
    results: list = []

    def models():
        assert (config.models_dir() / "lama.onnx").exists(), "lama.onnx missing"
        assert (config.models_dir() / "text_det.onnx").exists(), "text_det.onnx missing"
        inp = get_inpainter()
        return f"providers={inp.gpu_providers() or ['CPU']}"

    def image():
        img = imaging.load(str(samples / "photo-logo.jpg"))
        mask = np.zeros(img.bgr.shape[:2], np.uint8)
        h, w = mask.shape
        mask[h - 100:h - 25, w - 290:w - 10] = 255
        for mode in ("fast", "ai"):
            out = get_inpainter().inpaint(img.bgr, mask, mode)
            assert out.shape == img.bgr.shape
            imaging.save(img, out, str(tmp / f"logo-{mode}.jpg"))
        return f"device={get_inpainter().device_name()}"

    def text_detection():
        bgr = imaging.load_bgr(str(samples / "photo-tiled-watermark.jpg"))
        mask = detect.detect_text_mask(bgr)
        coverage = float((mask > 0).mean())
        assert coverage > 0.01, f"coverage {coverage:.3f}"
        return f"coverage={coverage:.3f}"

    def video_pipeline():
        clip = tmp / "clip.mp4"
        proc = run(["-y", "-i", str(samples / "clip-moving-watermark.mp4"), "-t", "1.2", "-c:v", "libx264", "-c:a", "aac", str(clip)])
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-300:]
        info, regions = video.detect_overlays(str(clip))
        assert regions, "no watermark detected"
        out = tmp / "clip-clean.mp4"
        video.remove_watermark(str(clip), str(out), [video.Region(r["start"], r["end"], r["mask"]) for r in regions], "fast")
        result = probe(str(out))
        assert result.has_audio and abs(result.duration - info.duration) < 0.3
        return f"ffmpeg={ffmpeg_exe()}"

    def pdf_pipeline():
        analysis = pdf.analyze(str(samples / "report-confidential.pdf"))
        keys = {analysis["_keys"][c["id"]] for c in analysis["candidates"] if c["selected"]}
        assert keys, "no candidates"
        out = tmp / "clean.pdf"
        pdf.remove(str(samples / "report-confidential.pdf"), str(out), keys)
        text = pdf.extract_text(str(out))
        assert "CONFIDENTIAL" not in text and "Quarterly report" in text
        pdf.render_page(str(out), 0, 300)
        return f"{len(keys)} watermark(s) removed"

    def office_pipeline():
        done = []
        for name in ("slides-evaluation.pptx", "agreement-draft.docx"):
            analysis = office.analyze(str(samples / name))
            keys = {analysis["_keys"][c["id"]] for c in analysis["candidates"] if c["selected"]}
            assert keys, f"nothing found in {name}"
            out = tmp / ("clean-" + name)
            office.remove(str(samples / name), str(out), keys)
            after = office.analyze(str(out))
            assert not any(c["selected"] for c in after["candidates"]), f"{name} still has watermarks"
            done.append(name)
        return ", ".join(done)

    def downloader():
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            ydl.build_format_selector(social.FORMAT)
        return f"yt-dlp {social.ytdlp_version()}"

    def server():
        from .main import start_server

        srv, store, jobs, url = start_server(desktop=False)
        try:
            token = url.split("t=")[1]
            base = url.split("/?")[0]
            req = urllib.request.Request(base + "/api/status", headers={"X-Token": token})
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = json.loads(resp.read())
            with urllib.request.urlopen(base + "/static/js/app.js", timeout=20) as resp:
                assert resp.status == 200
            return f"ai={status['ai_available']} device={status['device']}"
        finally:
            srv.should_exit = True
            jobs.shutdown()
            store.cleanup()

    def window_toolkit():
        if sys.platform != "win32":
            return "skipped (not Windows)"
        import webview  # noqa: F401

        return "pywebview import ok"

    for name, fn in [
        ("AI models", models), ("Image removal", image), ("Text detection", text_detection),
        ("Video pipeline", video_pipeline), ("PDF pipeline", pdf_pipeline), ("Office pipeline", office_pipeline),
        ("Downloader", downloader), ("Local server", server), ("Window toolkit", window_toolkit),
    ]:
        _check(name, fn, results)

    lines = []
    for ok, name, detail, seconds in results:
        first = detail.strip().splitlines()[0] if detail.strip() else ""
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name:<16} {seconds:6.1f}s  {first}")
        if not ok:
            lines.append(detail)
    report = "\n".join(lines)
    print(report, flush=True)
    (config.data_dir() / "selftest.log").write_text(report, encoding="utf-8")
    return 0 if all(r[0] for r in results) else 1
