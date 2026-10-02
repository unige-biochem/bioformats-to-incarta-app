"""Draws the app icon: the Bio-Formats layers, an arrow, and a well plate.

    uv run --no-project --with pymupdf --with pillow python tools/make_icon.py

The layers are cut from OME's own vector logo, fetched from the URL below, so
only the generated icons are committed. The well plate stands for IN Carta
without borrowing its logo. Writes into the package's assets/:

    app.ico           the shortcut's icon, every size Windows asks for
    icon-<n>.png      the same sizes for the window and the taskbar

Below 128 px the plate gets fewer, bigger wells; at 32 px and below three
shapes do not read, so those sizes drop the arrow and keep the layers over a
corner of the plate.
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

LOGO_PDF = "https://downloads.openmicroscopy.org/bio-formats/4.4.7/bio-formats-logo.pdf"
#: The three stacked layers, in the PDF's own coordinates (the rest is the
#: BIO-FORMATS wordmark).
LAYERS_CLIP = pymupdf.Rect(258, 258, 977, 1155)

ASSETS = Path(__file__).resolve().parent.parent / "src" / "bioformats_to_incarta_app" / "assets"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_SIZES = (16, 32, 48, 256)

NAVY = (28, 74, 135)        # the wordmark's blue
PLATE_LINE = (51, 71, 91)
PLATE_FILL = (232, 236, 241)
WELL = (74, 132, 190)
TILE_LINE = (208, 215, 222)

#: Everything is drawn at this size, then scaled down: Pillow does not
#: anti-alias shapes, resampling does.
CANVAS = 1024


def layers(height: int) -> Image.Image:
    with urllib.request.urlopen(LOGO_PDF, timeout=60) as response:
        page = pymupdf.open(stream=response.read(), filetype="pdf")[0]
    zoom = height / LAYERS_CLIP.height
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=LAYERS_CLIP,
                             alpha=True)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGBA")


def tile(draw: ImageDraw.ImageDraw, inset: int, radius: int, line: int) -> None:
    draw.rounded_rectangle((inset, inset, CANVAS - inset, CANVAS - inset),
                           radius=radius, fill="white", outline=TILE_LINE, width=line)


def plate(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
          rows: int, cols: int, line: int) -> None:
    """A microplate seen from above: A1's corner cut, wells in a grid."""
    left, top, right, bottom = box
    cut = (bottom - top) // 6
    outline = [(left + cut, top), (right, top), (right, bottom), (left, bottom),
               (left, top + cut)]
    draw.polygon(outline, fill=PLATE_FILL)
    draw.line([*outline, outline[0]], fill=PLATE_LINE, width=line, joint="curve")
    margin_x = (right - left) * 0.12
    margin_y = (bottom - top) * 0.16
    pitch_x = (right - left - 2 * margin_x) / cols
    pitch_y = (bottom - top - 2 * margin_y) / rows
    radius = min(pitch_x, pitch_y) * 0.36
    for row in range(rows):
        for col in range(cols):
            cx = left + margin_x + pitch_x * (col + 0.5)
            cy = top + margin_y + pitch_y * (row + 0.5)
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=WELL)


def arrow(draw: ImageDraw.ImageDraw, left: int, right: int, y: int, shaft: int) -> None:
    head = shaft * 2.4
    neck = right - head * 0.9
    draw.rectangle((left, y - shaft / 2, neck, y + shaft / 2), fill=NAVY)
    draw.polygon([(neck, y - head / 2), (right, y), (neck, y + head / 2)], fill=NAVY)


def full(mark: Image.Image, rows: int = 4, cols: int = 6) -> Image.Image:
    """Layers, arrow, plate - read left to right, as the conversion goes."""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    tile(draw, inset=24, radius=180, line=12)
    small = mark.resize((round(mark.width * 360 / mark.height), 360), Image.LANCZOS)
    canvas.alpha_composite(small, (70, (CANVAS - small.height) // 2))
    arrow(draw, left=70 + small.width + 34, right=572, y=CANVAS // 2, shaft=50)
    plate(draw, (604, 367, 962, 657), rows=rows, cols=cols, line=26)
    return canvas


def compact(mark: Image.Image) -> Image.Image:
    """For 32 px and below: a big plate, the layers over its top-left corner."""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    tile(draw, inset=16, radius=200, line=28)
    plate(draw, (300, 420, 960, 900), rows=3, cols=4, line=44)
    big = mark.resize((round(mark.width * 600 / mark.height), 600), Image.LANCZOS)
    canvas.alpha_composite(big, (70, 70))
    return canvas


def design(size: int) -> str:
    """Fewer, bigger wells as the icon shrinks, then no arrow at all."""
    return "full" if size >= 128 else "medium" if size > 32 else "compact"


def main() -> None:
    mark = layers(1200)
    designs = {"full": full(mark), "medium": full(mark, rows=3, cols=4),
               "compact": compact(mark)}
    images = {size: designs[design(size)].resize((size, size), Image.LANCZOS)
              for size in ICO_SIZES}
    ASSETS.mkdir(parents=True, exist_ok=True)
    images[256].save(ASSETS / "app.ico", sizes=[(s, s) for s in ICO_SIZES],
                     append_images=[images[s] for s in ICO_SIZES if s != 256])
    for size in PNG_SIZES:
        images[size].save(ASSETS / f"icon-{size}.png")
    print(f"wrote {ASSETS / 'app.ico'} and icon-{{{','.join(map(str, PNG_SIZES))}}}.png")


if __name__ == "__main__":
    main()
