# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build: pyinstaller --noconfirm --clean packaging/WatermarkRemover.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

for model in ("lama.onnx", "text_det.onnx"):
    if not (ROOT / "models" / model).exists():
        raise SystemExit(f"models/{model} is missing - run: python scripts/fetch_models.py")
if not (ROOT / "samples").is_dir():
    raise SystemExit("samples/ is missing - run: python scripts/make_samples.py")

datas = [
    (str(ROOT / "app" / "web"), "app/web"),
    (str(ROOT / "models" / "lama.onnx"), "models"),
    (str(ROOT / "models" / "text_det.onnx"), "models"),
    (str(ROOT / "samples"), "samples"),
]
datas += collect_data_files("imageio_ffmpeg", includes=["binaries/*"])
datas += collect_data_files("pptx")
datas += collect_data_files("docx")
datas += collect_data_files("pypdfium2")
datas += collect_data_files("pypdfium2_raw")
datas += collect_data_files("webview")

binaries = []
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("pypdfium2_raw")
binaries += collect_dynamic_libs("pillow_heif")

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview")
hiddenimports += ["pillow_heif", "clr", "multipart", "python_multipart"]

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WatermarkRemover",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WatermarkRemover", upx=False)
