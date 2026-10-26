"""Local HTTP API used by the app window (bound to 127.0.0.1, protected by a per-launch token)."""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, __version__, config
from .core import detect, imaging, office, pdf, social, video
from .core.ffmpeg import extract_frame, make_preview, probe
from .core.inpaint import get_inpainter
from .jobs import FileStore, Job, JobManager, StoredFile

log = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = office.PPT_EXTENSIONS | office.WORD_EXTENSIONS | office.LEGACY_EXTENSIONS
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
MAX_URL_DOWNLOAD = 4 * 1024 ** 3


def classify(name: str, path: Optional[str] = None) -> Optional[str]:
    ext = Path(name).suffix.lower()
    if ext == ".gif" and path and imaging.is_animated(path):
        return "video"
    if ext in imaging.IMAGE_EXTENSIONS:
        return "image"
    if ext in video.VIDEO_EXTENSIONS:
        return "video"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in OFFICE_EXTENSIONS:
        return "office"
    return None


def _data_url_bytes(data_url: str) -> bytes:
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


def _mask_data_url(mask: np.ndarray) -> str:
    return "data:image/png;base64," + base64.b64encode(imaging.encode_mask_png(mask)).decode()


def _result_name(name: str, ext: Optional[str] = None, suffix: str = "no-watermark") -> str:
    stem, original_ext = os.path.splitext(name)
    return f"{stem}-{suffix}{ext or original_ext}"


def create_app(token: str, store: FileStore, jobs: JobManager, desktop: bool = False) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    web = config.web_dir()
    app.mount("/static", StaticFiles(directory=str(web)), name="static")
    pdf_cache: dict[str, dict] = {}
    office_cache: dict[str, dict] = {}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-token") or request.query_params.get("t")
            if supplied != token:
                return JSONResponse({"detail": "Invalid token"}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------------ basics
    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse((web / "index.html").read_text(encoding="utf-8"))

    def file_info(stored: StoredFile) -> dict:
        info = stored.to_dict()
        try:
            if stored.kind == "image" and "width" not in stored.meta:
                img = imaging.load(stored.path)
                stored.meta.update(width=img.bgr.shape[1], height=img.bgr.shape[0], alpha=img.alpha is not None)
            elif stored.kind == "video" and "width" not in stored.meta:
                stored.meta.update(probe(stored.path).to_dict())
            elif stored.kind == "pdf" and "pages" not in stored.meta:
                stored.meta["pages"] = pdf.page_count(stored.path)
            elif stored.kind == "office" and "doc_type" not in stored.meta:
                stored.meta["doc_type"] = office.kind_of(stored.name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not open “{stored.name}”: {exc}") from exc
        info["meta"] = stored.meta
        return info

    def register(path: str, name: str, parent: Optional[str] = None) -> dict:
        kind = classify(name, path)
        if kind is None:
            raise HTTPException(400, f"“{Path(name).suffix or name}” files aren't supported.")
        return file_info(store.add(path, name, kind, parent))

    def get_file(file_id: str) -> StoredFile:
        try:
            return store.get(file_id)
        except KeyError as exc:
            raise HTTPException(404, "File not found – please open it again.") from exc

    def job_response(job: Job) -> dict:
        return job.to_dict()

    @app.get("/api/status")
    def status():
        inpainter = get_inpainter()
        return {
            "app": APP_NAME,
            "version": __version__,
            "desktop": desktop,
            "ai_available": inpainter.ai_available,
            "detector_available": detect.get_text_detector().available,
            "gpu": bool(inpainter.gpu_providers()),
            "device": inpainter.device_name(),
            "ytdlp": social.ytdlp_version(),
            "libreoffice": office.find_libreoffice() is not None,
            "settings": config.load_settings(),
            "platform": sys.platform,
        }

    @app.get("/api/settings")
    def get_settings():
        return config.load_settings()

    @app.put("/api/settings")
    def put_settings(changes: dict = Body(...)):
        before = config.load_settings()
        settings = config.save_settings(changes)
        if settings.get("device") != before.get("device"):
            get_inpainter().reset()
        return settings

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "file").name
        if classify(name) is None and Path(name).suffix.lower() != ".gif":
            raise HTTPException(400, f"“{Path(name).suffix or name}” files aren't supported.")
        dest = store.new_path(name)
        with open(dest, "wb") as fh:
            while chunk := await file.read(1 << 20):
                fh.write(chunk)
        return register(str(dest), name)

    @app.post("/api/open-path")
    def open_path(body: dict = Body(...)):
        path = body.get("path", "")
        if not desktop or not os.path.isfile(path):
            raise HTTPException(400, "File not found")
        return register(path, os.path.basename(path))

    @app.post("/api/fetch-url")
    def fetch_url(body: dict = Body(...)):
        url = social.extract_url(body.get("url", "")) or ""
        if not url:
            raise HTTPException(400, "That doesn't look like a link.")
        name = Path(urllib.parse.urlparse(url).path).name or "download"
        if classify(name) is None:
            # Not a direct file link: treat it as a social-media / video page.
            return {"social": True, "url": url}
        dest = store.new_path(name)
        request = urllib.request.Request(url, headers={"User-Agent": f"Mozilla/5.0 {APP_NAME}/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=60) as resp, open(dest, "wb") as fh:
                copied = 0
                while chunk := resp.read(1 << 20):
                    copied += len(chunk)
                    if copied > MAX_URL_DOWNLOAD:
                        raise HTTPException(400, "That file is too large to download.")
                    fh.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Couldn't download that link: {exc}") from exc
        return register(str(dest), name)

    @app.get("/api/files/{file_id}")
    def serve_file(file_id: str, download: int = 0):
        stored = get_file(file_id)
        headers = {}
        if download:
            quoted = urllib.parse.quote(stored.name)
            headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quoted}"
        return FileResponse(stored.path, media_type=stored.mime, headers=headers)

    @app.get("/api/files/{file_id}/info")
    def get_info(file_id: str):
        return file_info(get_file(file_id))

    @app.get("/api/files/{file_id}/preview")
    def preview(file_id: str, max_side: int = 4096):
        stored = get_file(file_id)
        if stored.kind != "image":
            raise HTTPException(400, "Not an image")
        img = imaging.load(stored.path)
        data, mime = imaging.to_preview_bytes(img.bgr, img.alpha, max_side)
        return Response(data, media_type=mime)

    @app.post("/api/save")
    def save(body: dict = Body(...)):
        """Copy a result to a chosen path (desktop) or the output folder."""
        stored = get_file(body.get("file_id", ""))
        target = body.get("path")
        if target:
            if not desktop:
                raise HTTPException(400, "Saving to a path is only available in the desktop app")
        else:
            folder = Path(config.load_settings().get("output_dir") or config.default_output_dir())
            folder.mkdir(parents=True, exist_ok=True)
            target = str(_unique(folder / stored.name))
        shutil.copyfile(stored.path, target)
        return {"path": target}

    @app.post("/api/reveal")
    def reveal(body: dict = Body(...)):
        path = body.get("path", "")
        if desktop and os.path.exists(path):
            _reveal_in_explorer(path)
        return {"ok": True}

    @app.get("/api/samples")
    def samples():
        folder = config.samples_dir()
        items = []
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                kind = classify(f.name, str(f))
                if kind:
                    items.append({"name": f.name, "kind": kind})
        return items

    @app.get("/api/samples/{name}/thumb")
    def sample_thumb(name: str):
        path = config.samples_dir() / Path(name).name
        if not path.is_file():
            raise HTTPException(404)
        kind = classify(path.name, str(path))
        if kind == "image":
            bgr = imaging.load_bgr(str(path))
        elif kind == "video":
            bgr = extract_frame(str(path), 1.0, 240)
        elif kind == "pdf":
            bgr = pdf.render_page(str(path), 0, 240)
        else:
            raise HTTPException(404)
        h, w = bgr.shape[:2]
        scale = 240 / max(h, w)
        bgr = cv2.resize(bgr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/samples/{name}/open")
    def open_sample(name: str):
        path = config.samples_dir() / Path(name).name
        if not path.is_file():
            raise HTTPException(404, "Sample not found")
        dest = store.new_path(path.name)
        shutil.copyfile(path, dest)
        return register(str(dest), path.name)

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def job_cancel(job_id: str):
        return {"ok": jobs.cancel(job_id)}

    # ------------------------------------------------------------------ images
    @app.post("/api/image/detect")
    def image_detect(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        bgr = imaging.load_bgr(stored.path)
        mask = detect.detect_text_mask(bgr)
        return {"mask": _mask_data_url(mask), "found": bool(mask.any())}

    @app.post("/api/image/detect-batch")
    def image_detect_batch(body: dict = Body(...)):
        files = [get_file(fid) for fid in body.get("file_ids", [])]
        images = [imaging.load_bgr(f.path) for f in files]
        mask = detect.detect_batch_overlay(images)
        return {"mask": _mask_data_url(mask) if mask is not None and mask.any() else None}

    def _image_mode(mode: Optional[str]) -> str:
        return mode if mode in ("ai", "fast") else config.load_settings().get("image_quality", "ai")

    @app.post("/api/image/remove")
    def image_remove(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        mask_data = _data_url_bytes(body.get("mask", ""))
        mode = _image_mode(body.get("mode"))

        def work(job: Job):
            img = imaging.load(stored.path)
            mask = imaging.decode_mask(mask_data, img.bgr.shape[:2])
            if not mask.any():
                raise ValueError("Paint over the watermark first (or use Auto-detect).")
            job.update(0.02, "Removing watermark…")
            result = get_inpainter().inpaint(
                img.bgr, mask, mode, progress=lambda p: job.update(0.02 + 0.93 * p), cancel=job.cancelled
            )
            ext = imaging.output_extension(img.source_ext, img.alpha is not None)
            name = _result_name(_original_name(stored), ext)
            out = store.new_path(name)
            imaging.save(img, result, str(out))
            return file_info(store.add(str(out), name, "image", parent=stored.id, meta={"original": _original_name(stored)}))

        return job_response(jobs.submit("image", work))

    @app.post("/api/image/remove-batch")
    def image_remove_batch(body: dict = Body(...)):
        files = [get_file(fid) for fid in body.get("file_ids", [])]
        mask_data = _data_url_bytes(body["mask"]) if body.get("mask") else None
        mode = _image_mode(body.get("mode"))

        def work(job: Job):
            results = []
            for i, stored in enumerate(files):
                job.update(i / len(files), f"Image {i + 1} of {len(files)}")
                img = imaging.load(stored.path)
                if mask_data is not None:
                    mask = imaging.decode_mask(mask_data, img.bgr.shape[:2])
                else:
                    mask = detect.detect_text_mask(img.bgr)
                if mask.any():
                    result = get_inpainter().inpaint(img.bgr, mask, mode, cancel=job.cancelled)
                else:
                    result = img.bgr
                ext = imaging.output_extension(img.source_ext, img.alpha is not None)
                name = _result_name(_original_name(stored), ext)
                out = store.new_path(name)
                imaging.save(img, result, str(out))
                info = file_info(store.add(str(out), name, "image", parent=stored.id))
                info["source_id"] = stored.id
                info["changed"] = bool(mask.any())
                results.append(info)
            return results

        return job_response(jobs.submit("image-batch", work))

    # ------------------------------------------------------------------ video
    @app.get("/api/video/{file_id}/frame")
    def video_frame(file_id: str, t: float = 0.0, w: int = 1280):
        stored = get_file(file_id)
        frame = extract_frame(stored.path, max(0.0, t), max_side=max(160, min(w, 3840)))
        if frame is None:
            raise HTTPException(400, "Could not read that frame")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return Response(buf.tobytes(), media_type="image/jpeg")

    @app.post("/api/video/playable")
    def video_playable(body: dict = Body(...)):
        """Return a file the built-in browser can play (transcoding a small preview if needed)."""
        stored = get_file(body.get("file_id", ""))
        info = file_info(stored)["meta"]
        ext = Path(stored.path).suffix.lower()
        if info.get("codec") in BROWSER_VIDEO and ext in (".mp4", ".m4v", ".webm", ".mov") and not body.get("force"):
            return {"file_id": stored.id, "job": None}
        if stored.meta.get("preview_id"):
            return {"file_id": stored.meta["preview_id"], "job": None}

        def work(job: Job):
            job.update(0.1, "Preparing preview…")
            out = store.new_path(Path(stored.name).stem + "-preview.mp4")
            make_preview(stored.path, str(out))
            preview = store.add(str(out), out.name, "video", parent=stored.id)
            stored.meta["preview_id"] = preview.id
            return {"file_id": preview.id}

        return {"file_id": None, "job": job_response(jobs.submit("preview", work))}

    @app.post("/api/video/detect")
    def video_detect(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))

        def work(job: Job):
            info, regions = video.detect_overlays(
                stored.path, progress=lambda p, m: job.update(p, m), cancel=job.cancelled
            )
            return {
                "regions": [
                    {"start": round(r["start"], 2), "end": round(min(r["end"], info.duration), 2), "mask": _mask_data_url(r["mask"])}
                    for r in regions
                ]
            }

        return job_response(jobs.submit("video-detect", work))

    @app.post("/api/video/remove")
    def video_remove(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        info = file_info(stored)["meta"]
        regions = []
        for r in body.get("regions", []):
            mask = imaging.decode_mask(_data_url_bytes(r["mask"]), (info["height"], info["width"]))
            if mask.any():
                end = float(r.get("end", info["duration"]))
                if end >= info["duration"] - 0.05:
                    end = 1e9
                regions.append(video.Region(float(r.get("start", 0)), end, mask))
        if not regions:
            raise HTTPException(400, "Mark the watermark first (paint over it or use Auto-detect).")
        mode = body.get("mode") or config.load_settings().get("video_quality", "auto")
        crf = int(config.load_settings().get("video_crf", 18))

        def work(job: Job):
            ext = ".gif" if Path(stored.name).suffix.lower() == ".gif" else ".mp4"
            name = _result_name(_original_name(stored), ext)
            out = store.new_path(name)
            job.update(0.0, "Starting…")
            video.remove_watermark(
                stored.path, str(out), regions, mode, crf, progress=lambda p, m: job.update(p, m), cancel=job.cancelled
            )
            return file_info(store.add(str(out), name, "video", parent=stored.id))

        return job_response(jobs.submit("video", work))

    # ------------------------------------------------------------------ PDF
    @app.post("/api/pdf/analyze")
    def pdf_analyze(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))

        def work(job: Job):
            job.update(0.2, "Reading the PDF…")
            result = pdf.analyze(stored.path)
            pdf_cache[stored.id] = result
            return {k: v for k, v in result.items() if not k.startswith("_")}

        return job_response(jobs.submit("pdf-analyze", work))

    def _pdf_keys(file_id: str, ids) -> set[str]:
        analysis = pdf_cache.get(file_id)
        if analysis is None:
            analysis = pdf_cache[file_id] = pdf.analyze(get_file(file_id).path)
        return {analysis["_keys"][i] for i in ids if i in analysis["_keys"]}

    @app.get("/api/pdf/{file_id}/page/{index}")
    def pdf_page(file_id: str, index: int, w: int = 900, remove: str = ""):
        stored = get_file(file_id)
        ids = [i for i in remove.split(",") if i]
        keys = _pdf_keys(file_id, ids) if ids else None
        try:
            bgr = pdf.render_page(stored.path, index, max(100, min(w, 2400)), keys)
        except IndexError as exc:
            raise HTTPException(404, "No such page") from exc
        ok, buf = cv2.imencode(".png" if w <= 400 else ".jpg", bgr)
        return Response(buf.tobytes(), media_type="image/png" if w <= 400 else "image/jpeg")

    @app.post("/api/pdf/remove")
    def pdf_remove(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        keys = _pdf_keys(stored.id, body.get("ids", []))
        if not keys:
            raise HTTPException(400, "Tick at least one watermark to remove.")

        def work(job: Job):
            job.update(0.3, "Removing watermarks…")
            name = _result_name(_original_name(stored))
            out = store.new_path(name)
            pdf.remove(stored.path, str(out), keys)
            return file_info(store.add(str(out), name, "pdf", parent=stored.id))

        return job_response(jobs.submit("pdf-remove", work))

    @app.post("/api/pdf/raster-detect")
    def pdf_raster_detect(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))

        def work(job: Job):
            job.update(0.2, "Comparing pages…")
            mask = pdf.detect_raster_overlay(stored.path)
            if mask is None or not mask.any():
                bgr = pdf.render_page(stored.path, int(body.get("page", 0)), 900)
                mask = detect.detect_text_mask(bgr)
                return {"mask": _mask_data_url(mask) if mask.any() else None, "method": "text"}
            return {"mask": _mask_data_url(mask), "method": "pages"}

        return job_response(jobs.submit("pdf-raster-detect", work))

    @app.post("/api/pdf/raster-remove")
    def pdf_raster_remove(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        mask_png = _data_url_bytes(body.get("mask", ""))
        mask = cv2.imdecode(np.frombuffer(mask_png, np.uint8), cv2.IMREAD_UNCHANGED)
        if mask is None:
            raise HTTPException(400, "Paint over the watermark first.")
        pages = body.get("pages")
        mode = _image_mode(body.get("mode"))

        def work(job: Job):
            name = _result_name(_original_name(stored))
            out = store.new_path(name)
            pdf.raster_remove(
                stored.path, str(out), mask, pages if isinstance(pages, list) else None,
                dpi=int(body.get("dpi", 200)), mode=mode, progress=lambda p, m: job.update(p, m), cancel=job.cancelled,
            )
            return file_info(store.add(str(out), name, "pdf", parent=stored.id))

        return job_response(jobs.submit("pdf-raster", work))

    # ------------------------------------------------------------------ Office
    @app.post("/api/office/analyze")
    def office_analyze(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))

        def work(job: Job):
            target = stored
            if office.kind_of(stored.name) == "legacy":
                job.update(0.1, "Converting to a modern format…")
                out_dir = store.new_path("converted").parent
                converted = office.convert_legacy(stored.path, str(out_dir))
                target = store.add(converted, Path(converted).name, "office", parent=stored.id)
            job.update(0.4, "Looking for watermarks…")
            result = office.analyze(target.path)
            office_cache[target.id] = result
            data = {k: v for k, v in result.items() if not k.startswith("_")}
            data["file"] = file_info(target)
            return data

        return job_response(jobs.submit("office-analyze", work))

    @app.post("/api/office/remove")
    def office_remove(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        analysis = office_cache.get(stored.id) or office.analyze(stored.path)
        keys = {analysis["_keys"][i] for i in body.get("ids", []) if i in analysis["_keys"]}
        name = _result_name(_original_name(stored))
        out = store.new_path(name)
        office.remove(stored.path, str(out), keys)
        result = store.add(str(out), name, "office", parent=stored.id, meta={"original": _original_name(stored)})
        office_cache[result.id] = office.analyze(str(out))
        return file_info(result)

    @app.get("/api/office/{file_id}/media")
    def office_media(file_id: str, name: str):
        stored = get_file(file_id)
        try:
            blob = office.get_media(stored.path, name)
        except KeyError as exc:
            raise HTTPException(404, "Picture not found") from exc
        bgr = imaging.image_bytes_to_bgr(blob)
        ok, buf = cv2.imencode(".png", bgr)
        return Response(buf.tobytes(), media_type="image/png")

    @app.post("/api/office/media-to-image")
    def office_media_to_image(body: dict = Body(...)):
        """Copy an embedded picture out as an image file so the image editor can clean it."""
        stored = get_file(body.get("file_id", ""))
        name = body.get("name", "")
        blob = office.get_media(stored.path, name)
        out = store.new_path(Path(name).name)
        out.write_bytes(blob)
        return register(str(out), Path(name).name)

    @app.post("/api/office/replace-media")
    def office_replace_media(body: dict = Body(...)):
        stored = get_file(body.get("file_id", ""))
        image = get_file(body.get("image_file_id", ""))
        name = _result_name(_original_name(stored))
        out = store.new_path(name)
        office.replace_media(stored.path, str(out), body.get("name", ""), Path(image.path).read_bytes())
        result = store.add(str(out), name, "office", parent=stored.id, meta={"original": _original_name(stored)})
        analysis = office.analyze(str(out))
        office_cache[result.id] = analysis
        data = {k: v for k, v in analysis.items() if not k.startswith("_")}
        data["file"] = file_info(result)
        return data

    # ------------------------------------------------------------------ social
    @app.post("/api/social/info")
    def social_info(body: dict = Body(...)):
        url = social.extract_url(body.get("url", ""))
        if not url:
            raise HTTPException(400, "Paste a link to a video (for example a TikTok or Instagram link).")
        return job_response(jobs.submit("social-info", lambda job: social.get_info(url), social.friendly_error))

    @app.post("/api/social/download")
    def social_download(body: dict = Body(...)):
        url = social.extract_url(body.get("url", ""))
        if not url:
            raise HTTPException(400, "Paste a link to a video first.")

        def work(job: Job):
            job.update(0.02, "Connecting…")
            out_dir = store.new_path("download").parent
            path, info = social.download(url, str(out_dir), progress=lambda p, m: job.update(p, m), cancel=job.cancelled)
            stored = store.add(path, Path(path).name, "video", meta={"source_url": url})
            data = file_info(stored)
            data["social"] = info
            return data

        return job_response(jobs.submit("social-download", work, social.friendly_error))

    @app.post("/api/social/update")
    def social_update():
        def work(job: Job):
            version = social.update_ytdlp(progress=lambda p, m: job.update(p, m))
            return {"version": version, "restart": True}

        return job_response(jobs.submit("social-update", work))

    return app


def _original_name(stored: StoredFile) -> str:
    """Keep the user's file name through repeated edits (“photo-no-watermark.jpg”, not “…-no-watermark-no-watermark…”)."""
    name = stored.meta.get("original") or stored.name
    return re.sub(r"(-no-watermark)+(?=\.[^.]+$)", "", name)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _reveal_in_explorer(path: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError as exc:
        log.warning("Could not open folder: %s", exc)
