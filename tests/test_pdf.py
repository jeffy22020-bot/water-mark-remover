from app.core import pdf


def test_finds_and_removes_pdf_watermarks(samples, tmp_path):
    src = samples / "report-confidential.pdf"
    analysis = pdf.analyze(str(src))
    labels = {c["label"]: c for c in analysis["candidates"]}
    assert labels["Text “CONFIDENTIAL”"]["selected"]
    assert any(c["kind"] == "marked" and c["selected"] for c in analysis["candidates"])
    assert any(c["kind"] == "image" for c in analysis["candidates"])

    keys = {analysis["_keys"][c["id"]] for c in analysis["candidates"]}
    out = tmp_path / "clean.pdf"
    pdf.remove(str(src), str(out), keys)
    text = pdf.extract_text(str(out))
    assert "CONFIDENTIAL" not in text
    assert "SAMPLE COPY" not in text
    assert "Quarterly report" in text
    assert pdf.analyze(str(out))["candidates"] == []


def test_preview_with_removal_renders(samples):
    src = samples / "report-confidential.pdf"
    analysis = pdf.analyze(str(src))
    keys = {analysis["_keys"][c["id"]] for c in analysis["candidates"] if c["selected"]}
    before = pdf.render_page(str(src), 0, 400)
    after = pdf.render_page(str(src), 0, 400, keys)
    assert before.shape == after.shape
    assert (before != after).any()


def test_raster_mode_rebuilds_pdf(samples, tmp_path):
    import numpy as np

    src = samples / "report-confidential.pdf"
    mask = np.zeros((1188, 868), np.uint8)
    mask[300:800, 100:700] = 255
    out = tmp_path / "raster.pdf"
    pdf.raster_remove(str(src), str(out), mask, pages=[0], dpi=72, mode="fast")
    assert pdf.page_count(str(out)) == 3
