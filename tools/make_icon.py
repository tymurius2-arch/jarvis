"""Рисует значок Джарвиса (реактор Тони Старка): python tools/make_icon.py"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # рисуем крупно, потом уменьшаем — так края гладкие
C = S / 2
CYAN = (70, 215, 255)


def ring(draw, r, w, color):
    draw.ellipse((C - r, C - r, C + r, C + r), outline=color, width=w)


def make() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # тёмный диск с тонкой металлической окантовкой
    d = ImageDraw.Draw(img)
    d.ellipse((24, 24, S - 24, S - 24), fill=(8, 16, 26, 255))
    ring(d, C - 30, 18, (110, 135, 155, 255))
    ring(d, C - 52, 8, (40, 60, 78, 255))

    # свечение
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((C - 330, C - 330, C + 330, C + 330), fill=CYAN + (120,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(70)))

    d = ImageDraw.Draw(img)
    # 10 сегментов-катушек
    for i in range(10):
        a0 = i * 36 - 90 + 4
        d.arc((C - 360, C - 360, C + 360, C + 360), a0, a0 + 28, fill=(150, 230, 255, 255), width=70)
    ring(d, 300, 10, (200, 245, 255, 255))
    ring(d, 395, 10, CYAN + (255,))

    # треугольник (как в реакторе Mark VI)
    pts = [(C + 215 * math.cos(math.radians(-90 + 120 * k)), C + 215 * math.sin(math.radians(-90 + 120 * k)))
           for k in range(3)]
    d.polygon(pts, outline=(170, 240, 255, 255), width=26)

    # яркое ядро
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    cd.ellipse((C - 170, C - 170, C + 170, C + 170), fill=CYAN + (255,))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    d.ellipse((C - 105, C - 105, C + 105, C + 105), fill=(235, 252, 255, 255))
    return img


def main():
    big = make()
    (ROOT / "jarvis" / "ui").mkdir(parents=True, exist_ok=True)
    (ROOT / "assets").mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "jarvis" / "ui" / "icon.png")
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "jarvis.ico", sizes=sizes)
    big.resize((512, 512), Image.LANCZOS).save(ROOT / "assets" / "jarvis.png")
    print("значок готов")


if __name__ == "__main__":
    main()
