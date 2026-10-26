"""Watermark Remover desktop application."""

import os

# Everything stays on this PC: opt out of ONNX Runtime usage telemetry before it is imported.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

__version__ = "1.0.0"
APP_NAME = "Watermark Remover"
