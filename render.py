"""Render a colored terminal capture (ANSI SGR) to a PNG, for /screen."""

from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageFont

# 16-colour ANSI palette (xterm-ish).
BASE16 = [
    (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
    (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
    (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
    (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
]
DEFAULT_FG = (220, 220, 220)
DEFAULT_BG = (24, 24, 24)

FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFontMono-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
FONT_SIZE = 18


def _load_font() -> ImageFont.FreeTypeFont:
    import os
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, FONT_SIZE)
    # Fall back to any installed JetBrains/DejaVu mono.
    import glob
    for pat in ("/usr/share/fonts/**/*Mono*.ttf",):
        for path in glob.glob(pat, recursive=True):
            try:
                return ImageFont.truetype(path, FONT_SIZE)
            except OSError:
                continue
    return ImageFont.load_default()


def _xterm256(n: int) -> tuple[int, int, int]:
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n // 6) % 6, n % 6
        steps = [0, 95, 135, 175, 215, 255]
        return steps[r], steps[g], steps[b]
    v = 8 + (n - 232) * 10
    return v, v, v


def _parse_cells(text: str):
    """Yield rows; each row is a list of (char, fg, bg)."""
    fg, bg, bold = DEFAULT_FG, DEFAULT_BG, False
    rows = []
    for line in text.split("\n"):
        row = []
        pos = 0
        for m in SGR_RE.finditer(line):
            for ch in line[pos:m.start()]:
                row.append((ch, fg, bg))
            pos = m.end()
            codes = [int(c) for c in m.group(1).split(";") if c != ""] or [0]
            i = 0
            while i < len(codes):
                c = codes[i]
                if c == 0:
                    fg, bg, bold = DEFAULT_FG, DEFAULT_BG, False
                elif c == 1:
                    bold = True
                elif c == 22:
                    bold = False
                elif 30 <= c <= 37:
                    fg = BASE16[c - 30 + (8 if bold else 0)]
                elif 90 <= c <= 97:
                    fg = BASE16[c - 90 + 8]
                elif 40 <= c <= 47:
                    bg = BASE16[c - 40]
                elif 100 <= c <= 107:
                    bg = BASE16[c - 100 + 8]
                elif c == 39:
                    fg = DEFAULT_FG
                elif c == 49:
                    bg = DEFAULT_BG
                elif c in (38, 48):
                    target_fg = c == 38
                    if i + 1 < len(codes) and codes[i + 1] == 5:
                        col = _xterm256(codes[i + 2]) if i + 2 < len(codes) else DEFAULT_FG
                        i += 2
                    elif i + 1 < len(codes) and codes[i + 1] == 2:
                        col = tuple(codes[i + 2:i + 5]) if i + 4 < len(codes) else DEFAULT_FG
                        i += 4
                    else:
                        col = DEFAULT_FG
                    if target_fg:
                        fg = col
                    else:
                        bg = col
                i += 1
        for ch in line[pos:]:
            row.append((ch, fg, bg))
        rows.append(row)
    return rows


def render_png(colored_text: str) -> bytes:
    font = _load_font()
    bbox = font.getbbox("M")
    cw = bbox[2] - bbox[0] or FONT_SIZE // 2
    ch = int((bbox[3] - bbox[1]) * 1.6) or FONT_SIZE
    rows = _parse_cells(colored_text)
    cols = max((len(r) for r in rows), default=1)
    pad = 8
    img = Image.new("RGB", (cols * cw + 2 * pad, len(rows) * ch + 2 * pad), DEFAULT_BG)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(rows):
        for x, (c, fg, bg) in enumerate(row):
            px, py = pad + x * cw, pad + y * ch
            if bg != DEFAULT_BG:
                draw.rectangle([px, py, px + cw, py + ch], fill=bg)
            if c != " ":
                draw.text((px, py), c, font=font, fill=fg)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
