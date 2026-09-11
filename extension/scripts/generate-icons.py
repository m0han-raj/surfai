"""Generate the SurfAI mark at every size the project needs.

The geometry lives here once and is emitted as both SVG (editable source of
truth) and PNG (what Chrome and the Web Store consume), so the raster and vector
forms can never drift.

Design notes
------------
The mark is a single breaking-wave stroke inside a squircle. One stroke,
because the smallest size this has to survive is 16x16 in a browser toolbar,
where any second element merges into mud.

Two things are done deliberately rather than by formula:

* **Optical stroke weight.** A stroke that is mathematically proportional looks
  thin at 16px and heavy at 512px, so small sizes get a slightly fatter stroke.
* **Optical margin.** Small sizes also get less padding, because at 16px a
  proportional margin wastes pixels the mark needs.

Anti-aliasing comes from the distance field rather than supersampling, which
is both smoother and fast enough to stay dependency-free. Nothing here runs at
request time.

    python extension/scripts/generate-icons.py
"""

from __future__ import annotations

import math
import pathlib
import struct
import zlib

# --- palette -------------------------------------------------------------
# Matches --accent in extension/src/styles/theme.css.
ACCENT = (37, 99, 235)
ACCENT_DEEP = (29, 78, 216)
WHITE = (255, 255, 255)
INK = (24, 24, 27)

# Everything lands in the extension's icon directory: the four sizes Chrome
# loads, the larger ones a store listing needs, and the editable SVG source.
ICONS = pathlib.Path(__file__).resolve().parent.parent / "public" / "icons"

# Sizes Chrome asks for, plus the store and marketing sizes.
EXTENSION_SIZES = (16, 32, 48, 128)
MARKETING_SIZES = (256, 512)

# --- geometry ------------------------------------------------------------
# A breaking wave, as two cubic beziers in a unit square: a long run-up that
# rises into a crest, then curls over and hooks back.
#
# An earlier version was a symmetric tilde. It was legible but read as a maths
# symbol rather than a wave, so the crest was made asymmetric and given a curl.
# The hook deliberately stops short of closing against the run-up: at 16px a
# closed loop fills in and becomes a blob.
WAVE = [
    ((0.08, 0.74), (0.24, 0.74), (0.26, 0.26), (0.58, 0.26)),
    ((0.58, 0.26), (0.84, 0.26), (0.84, 0.60), (0.63, 0.59)),
]

# The wave is monotonic in x, so points are bucketed by x and a pixel only
# tests the buckets near it. Brute force is ~2 billion distance tests at 512px.
X_BUCKETS = 64


def bezier(p0, p1, p2, p3, t):
    """Point on a cubic bezier at t."""
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def wave_points(samples: int = 320) -> list[tuple[float, float]]:
    """Dense polyline along the wave, used for distance testing."""
    points: list[tuple[float, float]] = []
    for segment in WAVE:
        for i in range(samples):
            points.append(bezier(*segment, i / (samples - 1)))
    return points


def stroke_half_width(size: int) -> float:
    """Half the stroke width, in unit-square terms, optically corrected.

    A constant ratio reads thin at 16px and heavy at 512px. This interpolates
    between two weights chosen by eye at each end.
    """
    small, large = 0.105, 0.070
    t = min(max((size - 16) / (512 - 16), 0.0), 1.0)
    return (small + (large - small) * t) / 2


def corner_radius(size: int) -> float:
    """Squircle corner radius as a fraction of the side."""
    return 0.225


def _rounded_distance(x: float, y: float, radius: float) -> float:
    """Signed distance to a rounded-square edge; positive inside."""
    dx = max(radius - x, x - (1.0 - radius), 0.0)
    dy = max(radius - y, y - (1.0 - radius), 0.0)
    return radius - math.hypot(dx, dy)


def _bucket(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Group points into X_BUCKETS bins by x, for local distance queries."""
    bins: list[list[tuple[float, float]]] = [[] for _ in range(X_BUCKETS)]
    for point in points:
        index = min(int(point[0] * X_BUCKETS), X_BUCKETS - 1)
        bins[index].append(point)
    return bins


def render(size: int, *, background: bool = True, mono: tuple | None = None) -> bytes:
    """Render the mark to RGBA bytes.

    Anti-aliasing comes from the distance field rather than supersampling: a
    pixel's coverage is its distance to the stroke edge, clamped across one
    pixel width. That is both smoother and far cheaper than 16x sampling.
    """
    half = stroke_half_width(size)
    radius = corner_radius(size)

    # A margin that shrinks at small sizes, where pixels are scarce.
    margin = 0.10 if size >= 48 else 0.06
    span = 1.0 - 2 * margin

    placed = [
        (margin + px * span, margin + (py - 0.14) * span + 0.14) for px, py in wave_points()
    ]
    bins = _bucket(placed)
    half_scaled = half * span

    fg = mono or WHITE
    bg_top = mono or ACCENT
    bg_bottom = mono or ACCENT_DEEP

    # One pixel, in unit-square terms: the width the edge is feathered over.
    feather = 1.0 / size
    # How many buckets to each side could hold a point within reach.
    reach = int(math.ceil((half_scaled + feather) * X_BUCKETS)) + 1

    rows = []
    for py in range(size):
        y = (py + 0.5) / size
        row = bytearray([0])

        for px in range(size):
            x = (px + 0.5) / size

            centre = min(int(x * X_BUCKETS), X_BUCKETS - 1)
            best = float("inf")
            for b in range(max(0, centre - reach), min(X_BUCKETS, centre + reach + 1)):
                for wx, wy in bins[b]:
                    d = (x - wx) ** 2 + (y - wy) ** 2
                    if d < best:
                        best = d
            distance = math.sqrt(best)

            # Coverage of the stroke, feathered across one pixel.
            stroke_cover = _clamp((half_scaled - distance) / feather + 0.5)

            if background:
                plate_cover = _clamp(_rounded_distance(x, y, radius) / feather + 0.5)
            else:
                plate_cover = 0.0

            alpha = max(stroke_cover, plate_cover)
            if alpha <= 0.0:
                row += bytes((0, 0, 0, 0))
                continue

            if background:
                base = (
                    bg_top[0] + (bg_bottom[0] - bg_top[0]) * y,
                    bg_top[1] + (bg_bottom[1] - bg_top[1]) * y,
                    bg_top[2] + (bg_bottom[2] - bg_top[2]) * y,
                )
            else:
                base = fg

            # Composite the stroke over the plate.
            mix = stroke_cover if background else 1.0
            row += bytes(
                (
                    int(base[0] + (fg[0] - base[0]) * mix),
                    int(base[1] + (fg[1] - base[1]) * mix),
                    int(base[2] + (fg[2] - base[2]) * mix),
                    int(255 * alpha),
                )
            )
        rows.append(bytes(row))

    return encode_png(size, size, b"".join(rows))


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def encode_png(width: int, height: int, raw: bytes) -> bytes:
    """Minimal RGBA PNG encoder."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def svg(size: int = 512, *, background: bool = True) -> str:
    """The same geometry as SVG, for editing and for any vector context."""
    margin = 0.10
    span = 1.0 - 2 * margin

    def place(p):
        return (
            round((margin + p[0] * span) * size, 2),
            round((margin + (p[1] - 0.14) * span + 0.14) * size, 2),
        )

    (s0, c1, c2, e1), (_, c3, c4, e2) = WAVE
    path = (
        f"M {place(s0)[0]} {place(s0)[1]} "
        f"C {place(c1)[0]} {place(c1)[1]}, {place(c2)[0]} {place(c2)[1]}, "
        f"{place(e1)[0]} {place(e1)[1]} "
        f"C {place(c3)[0]} {place(c3)[1]}, {place(c4)[0]} {place(c4)[1]}, "
        f"{place(e2)[0]} {place(e2)[1]}"
    )
    stroke = round(stroke_half_width(size) * 2 * span * size, 2)
    radius = round(corner_radius(size) * size, 2)

    backdrop = (
        f'  <defs>\n'
        f'    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">\n'
        f'      <stop offset="0%" stop-color="rgb{ACCENT}"/>\n'
        f'      <stop offset="100%" stop-color="rgb{ACCENT_DEEP}"/>\n'
        f'    </linearGradient>\n'
        f'  </defs>\n'
        f'  <rect width="{size}" height="{size}" rx="{radius}" fill="url(#g)"/>\n'
        if background
        else ""
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" aria-label="SurfAI">\n'
        f"{backdrop}"
        f'  <path d="{path}" fill="none" stroke="{"white" if background else f"rgb{ACCENT}"}" '
        f'stroke-width="{stroke}" stroke-linecap="round"/>\n'
        f"</svg>\n"
    )


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    root = ICONS.parents[2]

    for size in EXTENSION_SIZES + MARKETING_SIZES:
        path = ICONS / f"icon-{size}.png"
        path.write_bytes(render(size))
        print(f"  {path.relative_to(root)}  ({path.stat().st_size:,} bytes)")

    mark = ICONS / "mark-mono.png"
    mark.write_bytes(render(256, background=False, mono=INK))
    print(f"  {mark.relative_to(root)}  ({mark.stat().st_size:,} bytes)")

    for name, kwargs in (("logo.svg", {}), ("mark.svg", {"background": False})):
        path = ICONS / name
        path.write_text(svg(512, **kwargs), encoding="utf-8")
        print(f"  {path.relative_to(root)}")


if __name__ == "__main__":
    main()
