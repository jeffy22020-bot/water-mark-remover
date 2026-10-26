"""PDF watermark analysis and removal.

Most PDF watermarks are separate objects drawn on top of (or under) the page, so they
can be deleted exactly without touching the real content:

* ``/Artifact <</Subtype /Watermark>>`` marked content (Acrobat's "Add Watermark")
* content in an optional-content layer named like "Watermark"
* ``/Watermark`` and stamp annotations
* text drawn rotated / transparent / identically on many pages (iText, PDFBox, Word…)
* images or form XObjects stamped on many pages (logos, "Scanned with …" banners)

Each content stream is split into units (text objects, XObject draws, marked blocks and
graphics blocks). Units with the same signature across pages form one candidate; the user
picks candidates and we drop exactly those operators.

Scanned PDFs have the watermark baked into page images; for those, "raster mode" renders
every page, inpaints the marked area and rebuilds the PDF.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

import cv2
import numpy as np
import pikepdf
from pikepdf import Name

log = logging.getLogger(__name__)

KEYWORDS = re.compile(
    r"water\s*mark|confidential|draft|sample|preview|evaluation|trial|demo\b|unregistered|unlicen[cs]ed|"
    r"created (with|by|using)|generated (with|by|using)|scanned (with|by|using)|camscanner|do not (copy|distribute)|"
    r"©|\(c\)|copyright|all rights reserved|not for (sale|distribution|resale)|proof\b|specimen|\bvoid\b|"
    r"aspose|spire\.|itext|pdfelement|wondershare|foxit|nitro|smallpdf|ilovepdf|pdf24|sejda|docusign|pdfescape",
    re.I,
)
PATH_OPS = {"m", "l", "c", "v", "y", "re", "h"}
PAINT_OPS = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "sh"}
TEXT_SHOW = {"Tj", "TJ", "'", '"'}


@dataclass
class Unit:
    key: str
    kind: str  # text | image | form | marked | vector
    start: int
    end: int
    text: str = ""
    rotated: bool = False
    transparent: bool = False
    light: bool = False
    marker: bool = False
    font_size: float = 0.0
    obj: Optional[pikepdf.Object] = None


@dataclass
class Candidate:
    id: str
    key: str
    kind: str
    label: str
    pages: set = field(default_factory=set)
    occurrences: int = 0
    rotated: bool = False
    transparent: bool = False
    light: bool = False
    marker: bool = False
    keyword: bool = False
    font_size: float = 0.0
    score: float = 0.0
    reasons: list = field(default_factory=list)
    thumb: Optional[str] = None

    def to_dict(self, page_count: int) -> dict:
        return {
            "thumb": self.thumb,
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "pages": sorted(p + 1 for p in self.pages),
            "page_count": len(self.pages),
            "total_pages": page_count,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "selected": self.score >= 0.6,
        }


# ------------------------------------------------------------------ helpers
def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _is_rotated(a: float, b: float, c: float, d: float) -> bool:
    angle = math.degrees(math.atan2(b, a)) % 90
    return min(angle, 90 - angle) > 3 and (abs(b) + abs(c)) > 1e-3


def _resolve(resources, category: str, name) -> Optional[pikepdf.Object]:
    try:
        group = resources.get(Name("/" + category)) if resources is not None else None
        if group is None:
            return None
        return group.get(name if isinstance(name, Name) else Name(str(name)))
    except Exception:  # noqa: BLE001
        return None


def _stream_hash(obj) -> str:
    try:
        data = obj.read_raw_bytes()
    except Exception:  # noqa: BLE001
        data = repr(obj.objgen).encode()
    return hashlib.sha1(data).hexdigest()[:16]


class _FontDecoder:
    """Best-effort text decoding (ToUnicode CMaps, else WinAnsi) for labels."""

    _cache: dict = {}

    def __init__(self, font):
        self.two_byte = False
        self.map: dict[int, str] = {}
        if font is None:
            return
        try:
            self.two_byte = str(font.get("/Subtype")) == "/Type0"
            cmap = font.get("/ToUnicode")
            if cmap is not None:
                self._parse_cmap(cmap.read_bytes().decode("latin-1", "replace"))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _hex_to_str(h: str) -> str:
        data = bytes.fromhex(h if len(h) % 2 == 0 else h + "0")
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    def _parse_cmap(self, text: str) -> None:
        for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
            for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
                self.map[int(src, 16)] = self._hex_to_str(dst)
        for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
            for lo, hi, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
                lo_i, hi_i = int(lo, 16), int(hi, 16)
                if rest.startswith("["):
                    for offset, dst in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                        self.map[lo_i + offset] = self._hex_to_str(dst)
                else:
                    base = int(rest[1:-1] or "0", 16)
                    width = max(4, len(rest) - 2)
                    for code in range(lo_i, min(hi_i, lo_i + 4096) + 1):
                        self.map[code] = self._hex_to_str(format(base + code - lo_i, f"0{width}x"))

    def decode(self, data: bytes) -> str:
        if self.two_byte:
            codes = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]
        else:
            codes = list(data)
        if self.map:
            return "".join(self.map.get(c, "") for c in codes)
        if self.two_byte:
            return ""
        return bytes(codes).decode("cp1252", "replace")


def _decoder(font) -> _FontDecoder:
    key = font.objgen if font is not None and hasattr(font, "objgen") and font.objgen != (0, 0) else id(font)
    dec = _FontDecoder._cache.get(key)
    if dec is None:
        dec = _FontDecoder(font)
        if len(_FontDecoder._cache) > 512:
            _FontDecoder._cache.clear()
        _FontDecoder._cache[key] = dec
    return dec


def _is_watermark_props(props, resources) -> bool:
    if isinstance(props, Name):
        props = _resolve(resources, "Properties", props)
    if props is None or not isinstance(props, pikepdf.Dictionary):
        return False
    try:
        if str(props.get("/Subtype", "")) == "/Watermark":
            return True
        if str(props.get("/Type", "")) == "/OCG" and re.search(r"water\s*mark", str(props.get("/Name", "")), re.I):
            return True
        # Optional-content membership dictionaries list their OCGs.
        ocgs = props.get("/OCGs")
        if ocgs is not None:
            items = ocgs if isinstance(ocgs, pikepdf.Array) else [ocgs]
            return any(re.search(r"water\s*mark", str(o.get("/Name", "")), re.I) for o in items)
    except Exception:  # noqa: BLE001
        return False
    return False


# ------------------------------------------------------------------ segmentation
def segment(ops: list, resources) -> list[Unit]:
    """Split a parsed content stream into removable units."""
    units: list[Unit] = []
    gstack = [{"rot": False, "alpha": 1.0, "light": False}]
    q_starts: list[int] = []
    block_info: list[dict] = []  # per open q: has_text/has_do/has_paint/start
    marked: list[tuple[int, bool]] = []
    in_wm_marked = 0
    text = None

    for i, (operands, operator) in enumerate(ops):
        op = str(operator)
        g = gstack[-1]
        if op == "q":
            gstack.append(dict(g))
            q_starts.append(i)
            block_info.append({"start": i, "text": False, "do": False, "paint": False, "ops": []})
        elif op == "Q":
            if len(gstack) > 1:
                gstack.pop()
            if q_starts:
                q_starts.pop()
                info = block_info.pop()
                if (
                    not in_wm_marked
                    and not q_starts
                    and info["paint"]
                    and not info["text"]
                    and not info["do"]
                    and (g["rot"] or g["alpha"] < 0.99)
                ):
                    raw = repr(info["ops"]).encode()
                    units.append(
                        Unit(
                            "vec:" + hashlib.sha1(raw).hexdigest()[:16], "vector", info["start"], i,
                            rotated=g["rot"], transparent=g["alpha"] < 0.99, light=g["light"],
                        )
                    )
                if block_info:
                    for k in ("text", "do", "paint"):
                        block_info[-1][k] |= info[k]
        elif op == "cm" and len(operands) == 6:
            a, b, c, d = (_num(x) for x in operands[:4])
            if _is_rotated(a, b, c, d):
                g["rot"] = True
        elif op == "gs" and operands:
            state = _resolve(resources, "ExtGState", operands[0])
            if state is not None:
                for k in ("/ca", "/CA"):
                    if k in state:
                        g["alpha"] = min(g["alpha"], _num(state[k]))
        elif op in ("g", "rg", "k", "sc", "scn") and operands:
            vals = [_num(x) for x in operands if not isinstance(x, Name)]
            if vals:
                if op == "k" or len(vals) == 4:
                    lightness = 1 - max(vals)
                else:
                    lightness = min(vals)
                g["light"] = lightness > 0.6
        elif op in ("BDC", "BMC"):
            is_wm = op == "BDC" and len(operands) >= 2 and (
                _is_watermark_props(operands[1], resources)
            )
            if is_wm and not in_wm_marked:
                marked.append((i, True))
                in_wm_marked += 1
            else:
                marked.append((i, False))
        elif op == "EMC":
            if marked:
                start, is_wm = marked.pop()
                if is_wm:
                    in_wm_marked -= 1
                    raw = repr(ops[start:i + 1]).encode()
                    units.append(Unit("mc:" + hashlib.sha1(raw).hexdigest()[:16], "marked", start, i, marker=True))
        elif op == "BT":
            text = {"start": i, "font": None, "size": 0.0, "rot": g["rot"], "chunks": [], "decoded": []}
        elif text is not None and op == "Tf" and len(operands) >= 2:
            text["font"] = operands[0]
            text["size"] = abs(_num(operands[1]))
        elif text is not None and op == "Tm" and len(operands) == 6:
            a, b, c, d = (_num(x) for x in operands[:4])
            if _is_rotated(a, b, c, d):
                text["rot"] = True
            text["size"] = max(text["size"], text["size"] * math.hypot(a, b))
        elif text is not None and op in TEXT_SHOW:
            dec = _decoder(_resolve(resources, "Font", text["font"]) if text["font"] is not None else None)
            for operand in operands:
                items = operand if isinstance(operand, pikepdf.Array) else [operand]
                for item in items:
                    if isinstance(item, pikepdf.String):
                        raw = bytes(item)
                        text["chunks"].append(raw)
                        text["decoded"].append(dec.decode(raw))
        elif op == "ET" and text is not None:
            if not in_wm_marked and text["chunks"]:
                raw = str(text["font"]).encode() + b"|" + b"\x00".join(text["chunks"])
                units.append(
                    Unit(
                        "txt:" + hashlib.sha1(raw).hexdigest()[:16], "text", text["start"], i,
                        text="".join(text["decoded"]).strip(),
                        rotated=text["rot"], transparent=g["alpha"] < 0.99, light=g["light"],
                        font_size=text["size"],
                    )
                )
            if block_info:
                block_info[-1]["text"] = True
            text = None
        elif op == "Do" and operands:
            if block_info:
                block_info[-1]["do"] = True
            if in_wm_marked:
                continue
            xobj = _resolve(resources, "XObject", operands[0])
            if xobj is None:
                continue
            subtype = str(xobj.get("/Subtype", ""))
            kind = "image" if subtype == "/Image" else "form"
            transparent = g["alpha"] < 0.99 or (kind == "image" and "/SMask" in xobj)
            units.append(
                Unit(f"{kind}:{_stream_hash(xobj)}", kind, i, i, rotated=g["rot"], transparent=transparent, obj=xobj)
            )
        if block_info and op in PATH_OPS | PAINT_OPS:
            block_info[-1]["ops"].append((op, [repr(x) for x in operands]))
            if op in PAINT_OPS:
                block_info[-1]["paint"] = True
    return units


def _iter_streams(pdf: pikepdf.Pdf) -> Iterator[tuple[int, pikepdf.Object, pikepdf.Object, bool]]:
    """Yield (page_index, owner, resources, is_page) for pages and the form XObjects they use."""
    seen: set = set()

    def forms_of(resources, depth):
        if resources is None or depth > 3:
            return
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return
        for _, xobj in xobjects.items():
            try:
                if str(xobj.get("/Subtype", "")) != "/Form":
                    continue
            except Exception:  # noqa: BLE001
                continue
            yield xobj, depth

    for index, page in enumerate(pdf.pages):
        resources = page.obj.get("/Resources") or _inherited_resources(page)
        yield index, page, resources, True
        stack = list(forms_of(resources, 1))
        while stack:
            xobj, depth = stack.pop()
            og = xobj.objgen
            res = xobj.get("/Resources") or resources
            yield index, xobj, res, False
            if og in seen and og != (0, 0):
                continue
            seen.add(og)
            stack.extend(forms_of(res, depth + 1))


def _inherited_resources(page):
    node = page.obj
    while node is not None:
        if "/Resources" in node:
            return node["/Resources"]
        node = node.get("/Parent")
    return None


def _parse(owner) -> list:
    try:
        return list(pikepdf.parse_content_stream(owner))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not parse content stream: %s", exc)
        return []


# ------------------------------------------------------------------ analysis
def analyze(path: str, max_pages: int = 400) -> dict:
    with pikepdf.open(path) as pdf:
        page_count = len(pdf.pages)
        candidates: dict[str, Candidate] = {}
        form_units_seen: set = set()

        def add(unit_key, kind, page, label, **flags):
            cand = candidates.get(unit_key)
            if cand is None:
                cand = candidates[unit_key] = Candidate(f"c{len(candidates) + 1}", unit_key, kind, label)
            cand.pages.add(page)
            cand.occurrences += 1
            for k, v in flags.items():
                if k == "font_size":
                    cand.font_size = max(cand.font_size, v)
                elif v:
                    setattr(cand, k, True)

        for page_index, owner, resources, is_page in _iter_streams(pdf):
            if page_index >= max_pages:
                break
            if not is_page:
                marker = (owner.objgen, page_index)
                if marker in form_units_seen:
                    continue
                form_units_seen.add(marker)
            for unit in segment(_parse(owner), resources):
                label = _label_for(unit)
                add(
                    unit.key, unit.kind, page_index, label,
                    rotated=unit.rotated, transparent=unit.transparent, light=unit.light,
                    marker=unit.marker, keyword=bool(KEYWORDS.search(unit.text)), font_size=unit.font_size,
                )
                if unit.kind == "image" and candidates[unit.key].thumb is None:
                    candidates[unit.key].thumb = _thumbnail(unit.obj) or ""

            if is_page:
                for annot in owner.obj.get("/Annots", []) or []:
                    try:
                        subtype = str(annot.get("/Subtype", ""))
                    except Exception:  # noqa: BLE001
                        continue
                    contents = str(annot.get("/Contents", "")) if "/Contents" in annot else ""
                    if subtype == "/Watermark":
                        add("annot:/Watermark", "annotation", page_index, "Watermark annotation", marker=True)
                    elif subtype in ("/Stamp", "/FreeText"):
                        name = "Stamp" if subtype == "/Stamp" else "Text box"
                        add(
                            f"annot:{subtype}:{contents[:40]}", "annotation", page_index,
                            f"{name} annotation" + (f" “{contents[:40]}”" if contents else ""),
                            keyword=bool(KEYWORDS.search(contents)) or subtype == "/Stamp",
                        )

    result = []
    for cand in candidates.values():
        _score(cand, page_count)
        if cand.score >= 0.3:
            result.append(cand)
    result.sort(key=lambda c: (-c.score, -len(c.pages)))
    return {"page_count": page_count, "candidates": [c.to_dict(page_count) for c in result[:40]],
            "_keys": {c.id: c.key for c in result[:40]}}


def _thumbnail(obj) -> Optional[str]:
    import base64

    try:
        pil = pikepdf.PdfImage(obj).as_pil_image()
        pil.thumbnail((160, 160))
        buf = io.BytesIO()
        pil.convert("RGBA" if pil.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - exotic image encodings
        return None


def _label_for(unit: Unit) -> str:
    if unit.kind == "text":
        text = re.sub(r"\s+", " ", unit.text)[:60] or "(text in an embedded font)"
        return f"Text “{text}”"
    return {
        "image": "Image / logo",
        "form": "Stamped graphic",
        "marked": "Watermark layer (marked by the PDF creator)",
        "vector": "Vector graphic",
    }.get(unit.kind, unit.kind)


def _score(c: Candidate, page_count: int) -> None:
    score, reasons = 0.0, []
    share = len(c.pages) / max(1, page_count)
    if c.marker:
        score += 1.0
        reasons.append("tagged as a watermark")
    if c.keyword:
        score += 0.5
        reasons.append("watermark wording")
    if c.rotated:
        score += 0.4
        reasons.append("drawn at an angle")
    if c.transparent:
        score += 0.3
        reasons.append("see-through")
    if page_count >= 2 and share >= 0.5 and c.kind != "text" or (page_count >= 2 and share >= 0.8):
        score += 0.3
        reasons.append(f"on {len(c.pages)} of {page_count} pages")
    if c.kind == "text" and c.font_size >= 36:
        score += 0.2
        reasons.append("very large text")
    if c.light and c.kind in ("text", "vector"):
        score += 0.1
        reasons.append("light colour")
    if c.kind == "text" and not (c.rotated or c.transparent or c.keyword or c.marker):
        # Ordinary text repeated on every page (headers, footers) is rarely a watermark.
        score = min(score, 0.25)
    c.score, c.reasons = score, reasons


# ------------------------------------------------------------------ removal
def _remove_from(pdf: pikepdf.Pdf, keys: set[str]) -> int:
    removed = 0
    done: set = set()
    for _, owner, resources, is_page in _iter_streams(pdf):
        og = owner.objgen if not is_page else owner.obj.objgen
        if og in done and og != (0, 0):
            continue
        done.add(og)
        ops = _parse(owner)
        if not ops:
            continue
        drop: set[int] = set()
        for unit in segment(ops, resources):
            if unit.key in keys:
                drop.update(range(unit.start, unit.end + 1))
        if drop:
            new_ops = [op for i, op in enumerate(ops) if i not in drop]
            data = pikepdf.unparse_content_stream(new_ops)
            if is_page:
                owner.obj.Contents = pdf.make_stream(data)
            else:
                owner.write(data)
            removed += 1
        if is_page and "/Annots" in owner.obj:
            annots = owner.obj.Annots
            keep = pikepdf.Array()
            for annot in annots:
                subtype = str(annot.get("/Subtype", ""))
                contents = str(annot.get("/Contents", "")) if "/Contents" in annot else ""
                key = "annot:/Watermark" if subtype == "/Watermark" else f"annot:{subtype}:{contents[:40]}"
                if key in keys:
                    removed += 1
                    continue
                keep.append(annot)
            owner.obj.Annots = keep
    return removed


def remove(path: str, out_path: str, keys: set[str]) -> str:
    with pikepdf.open(path) as pdf:
        _remove_from(pdf, keys)
        pdf.save(out_path, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return out_path


def render_page(path_or_bytes, index: int, width: int = 900, keys: Optional[set[str]] = None) -> np.ndarray:
    """Render one page to BGR, optionally with candidate ``keys`` removed first."""
    import pypdfium2 as pdfium

    source = path_or_bytes
    if keys:
        with pikepdf.open(path_or_bytes) as src:
            single = pikepdf.new()
            single.pages.append(src.pages[index])
            _remove_from(single, keys)
            buf = io.BytesIO()
            single.save(buf)
        source, index = buf.getvalue(), 0
    doc = pdfium.PdfDocument(source)
    try:
        page = doc[index]
        scale = width / page.get_width()
        bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
        rgb = np.asarray(bitmap.to_pil().convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    finally:
        doc.close()


def page_count(path: str) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


def extract_text(path: str) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


# ------------------------------------------------------------------ raster mode
def detect_raster_overlay(path: str, width: int = 900) -> Optional[np.ndarray]:
    """Watermark baked into scanned pages: whatever stays put while page content changes."""
    from .detect import detect_batch_overlay

    count = page_count(path)
    if count < 3:
        return None
    step = max(1, count // 12)
    pages = [render_page(path, i, width) for i in range(0, count, step)][:12]
    h, w = pages[0].shape[:2]
    pages = [p for p in pages if p.shape[:2] == (h, w)]
    return detect_batch_overlay(pages)


def raster_remove(
    path: str,
    out_path: str,
    mask: np.ndarray,
    pages: Optional[list[int]] = None,
    dpi: int = 200,
    mode: str = "ai",
    progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> str:
    """Render pages, inpaint ``mask`` (relative to the page) and rebuild the PDF."""
    import pypdfium2 as pdfium

    from .inpaint import Cancelled, get_inpainter, normalize_mask

    inpainter = get_inpainter()
    doc = pdfium.PdfDocument(path)
    out = pikepdf.new()
    try:
        count = len(doc)
        targets = set(range(count)) if not pages else {p for p in pages if 0 <= p < count}
        with pikepdf.open(path) as original:
            for i in range(count):
                if cancel and cancel():
                    raise Cancelled()
                if i not in targets:
                    out.pages.append(original.pages[i])
                    continue
                page = doc[i]
                w_pt, h_pt = page.get_width(), page.get_height()
                bitmap = page.render(scale=dpi / 72, fill_color=(255, 255, 255, 255))
                bgr = cv2.cvtColor(np.asarray(bitmap.to_pil().convert("RGB")), cv2.COLOR_RGB2BGR)
                page_mask = normalize_mask(mask, bgr.shape[:2])
                cleaned = inpainter.inpaint(bgr, page_mask, mode)
                ok, jpg = cv2.imencode(".jpg", cleaned, [cv2.IMWRITE_JPEG_QUALITY, 92])
                image = pikepdf.Stream(out, jpg.tobytes())
                image.Type, image.Subtype = Name.XObject, Name.Image
                image.Width, image.Height = cleaned.shape[1], cleaned.shape[0]
                image.ColorSpace, image.BitsPerComponent, image.Filter = Name.DeviceRGB, 8, Name.DCTDecode
                content = f"q {w_pt:.3f} 0 0 {h_pt:.3f} 0 0 cm /Im0 Do Q".encode()
                new_page = pikepdf.Dictionary(
                    Type=Name.Page,
                    MediaBox=pikepdf.Array([0, 0, w_pt, h_pt]),
                    Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=out.make_indirect(image))),
                    Contents=out.make_stream(content),
                )
                out.pages.append(pikepdf.Page(new_page))
                if progress:
                    progress((i + 1) / count, f"Page {i + 1} of {count}")
        out.save(out_path)
    finally:
        doc.close()
    return out_path
