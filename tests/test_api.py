import time

import pytest
from fastapi.testclient import TestClient

from app.jobs import FileStore, JobManager
from app.server import create_app

TOKEN = "test-token"


@pytest.fixture()
def client(samples, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "samples_dir", lambda: samples)
    app = create_app(TOKEN, FileStore(), JobManager(), desktop=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["X-Token"] = TOKEN
        yield c


def wait(client, job):
    for _ in range(600):
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
        job = client.get(f"/api/jobs/{job['id']}").json()
    raise AssertionError("job timed out")


def test_requires_token(client):
    assert client.get("/api/status", headers={"X-Token": "nope"}).status_code == 403


def test_rejects_foreign_host(client):
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403


def test_status_and_index(client):
    assert client.get("/api/status").json()["version"]
    assert "Watermark Remover" in client.get("/").text


def test_image_flow(client, samples):
    with open(samples / "photo-logo.jpg", "rb") as fh:
        info = client.post("/api/upload", files={"file": ("photo.jpg", fh, "image/jpeg")}).json()
    assert info["kind"] == "image" and info["meta"]["width"] == 1200
    import base64

    import cv2
    import numpy as np

    mask = np.zeros((80, 120, 4), np.uint8)
    mask[70:78, 90:118, 3] = 255
    ok, png = cv2.imencode(".png", mask)
    job = client.post("/api/image/remove", json={
        "file_id": info["id"], "mask": "data:image/png;base64," + base64.b64encode(png.tobytes()).decode(), "mode": "fast",
    }).json()
    job = wait(client, job)
    assert job["status"] == "done", job
    result = job["result"]
    assert result["name"] == "photo-no-watermark.jpg"
    assert client.get(f"/api/files/{result['id']}").status_code == 200


def test_samples_and_pdf_flow(client):
    names = [s["name"] for s in client.get("/api/samples").json()]
    assert "report-confidential.pdf" in names
    info = client.post("/api/samples/report-confidential.pdf/open").json()
    job = wait(client, client.post("/api/pdf/analyze", json={"file_id": info["id"]}).json())
    ids = [c["id"] for c in job["result"]["candidates"] if c["selected"]]
    preview = client.get(f"/api/pdf/{info['id']}/page/0", params={"w": 300, "remove": ",".join(ids)})
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    job = wait(client, client.post("/api/pdf/remove", json={"file_id": info["id"], "ids": ids}).json())
    assert job["status"] == "done" and job["result"]["name"].endswith("-no-watermark.pdf")


def test_unsupported_file(client):
    r = client.post("/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_fetch_url_detects_file_type(client, samples):
    import http.server
    import threading

    data = (samples / "photo-logo.jpg").read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            html = self.path == "/page"
            body = b"<html>hi</html>" if html else data
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if html else "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        info = client.post("/api/fetch-url", json={"url": f"{base}/photo?id=1"}).json()
        assert info["kind"] == "image" and info["name"].endswith(".jpg")
        assert client.post("/api/fetch-url", json={"url": f"{base}/page"}).json()["social"] is True
        assert client.post("/api/fetch-url", json={"url": "Look https://vm.tiktok.com/ZM1/"}).json() == {
            "social": True, "url": "https://vm.tiktok.com/ZM1/"
        }
    finally:
        server.shutdown()
