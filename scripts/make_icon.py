"""Draw the app icon (packaging/icon.ico and app/web/img/icon.png)."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Diagonal yellow -> orange -> coral gradient, like the app's buttons.
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    stops = [(0.0, (252, 211, 77)), (0.52, (251, 143, 78)), (1.0, (255, 90, 95))]
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                if t <= t1:
                    k = (t - t0) / (t1 - t0)
                    px[x, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3)) + (255,)
                    break
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    w = size * 0.075

    def stroke(points, alpha):
        # Dense round dabs give a smooth stroke; drawn on a layer so overlaps don't stack alpha.
        layer = Image.new("L", (size, size), 0)
        ld = ImageDraw.Draw(layer)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / 2))
            for k in range(steps + 1):
                x, y = x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps
                ld.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)
        white = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        img.paste(white, (0, 0), layer.point(lambda v: v * alpha // 255))

    wave = [(size * (0.2 + 0.6 * i / 100), size * 0.34 + size * 0.06 * math.sin(i / 100 * 2 * math.pi * 1.5)) for i in range(101)]
    stroke(wave[:52], 110)
    stroke(wave[50:], 255)
    mountain = [(0.2, 0.74), (0.38, 0.54), (0.5, 0.64), (0.64, 0.46), (0.8, 0.7)]
    stroke([(x * size, y * size) for x, y in mountain], 255)
    return img


if __name__ == "__main__":
    icon = draw(512)
    (ROOT / "packaging").mkdir(exist_ok=True)
    icon.save(ROOT / "packaging" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    (ROOT / "app" / "web" / "img").mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(ROOT / "app" / "web" / "img" / "icon.png")
    print("icon written")
