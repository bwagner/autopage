#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["reportlab"]
# ///
"""
Fit a .txt onto the fewest PDF pages possible at the largest readable font size.

- Single-page mode: if the text fits at >= min_size, one page is produced with
  lines spread to fill the full height.
- Multi-page mode: when min_size forces overflow, the font is maximized
  (width-constrained, capped at --max-size) and lines are distributed evenly
  across as many pages as needed, each page filled top-to-bottom.

Usage:
  ./autopage.py input.txt output.pdf [--paper A4|LETTER] [--landscape]

Useful flags:
  --paper A4|LETTER         (default: A4)
  --landscape               (make it landscape)
  --margins 36,36,36,36     (points: top,right,bottom,left; default 36=0.5")
  --font Courier            (monospace font family)
  --tabsize 8               (tab expansion width)
  --min-size 10             (minimum font size in pt; may produce >1 page)
  --max-size N              (cap font size in pt)
"""

import math
import unicodedata

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

PAPER = {"A4": A4, "LETTER": LETTER}


MAX_LEADING_FACTOR = 1.5
FONT_SIZE_SEARCH_MAX = 500


def fit_text(
    input_path,
    output_path,
    paper="A4",
    landscape=False,
    margins=(36, 36, 36, 36),
    font="Courier",
    tabsize=8,
    min_size=10,
    max_size=None,
    max_leading=MAX_LEADING_FACTOR,
):
    top, right, bottom, left = margins
    pw, ph = PAPER[paper]
    if landscape:
        pw, ph = ph, pw
    uw, uh = pw - left - right, ph - top - bottom

    with open(input_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    # NFC: combine decomposed marks (e.g. "a"+U+0308) into precomposed chars
    # (ä) so Type 1 WinAnsi fonts like Courier can render them.
    lines = unicodedata.normalize("NFC", raw).expandtabs(tabsize).splitlines()
    if not lines:
        lines = [""]
    n = len(lines)

    # Largest font fitting the page width (and optional max_size cap).
    lo, hi = 1, max_size or FONT_SIZE_SEARCH_MAX
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if max((stringWidth(ln, font, mid) for ln in lines), default=0) <= uw:
            lo = mid
        else:
            hi = mid - 1
    size_w = lo

    # Single-page mode: font must also fit the height slot (uh / n).
    size_1page = min(size_w, int(uh / n))

    if size_1page >= min_size:
        # Fits comfortably on one page — spread lines to fill height.
        size = size_1page
        pages_lines = [lines]
    else:
        # Multi-page mode: maximise font (width-only), distribute evenly.
        size = max(min_size, size_w)
        # How many lines fit per page without glyph overlap when filling?
        max_lpp = max(1, int(uh / size))
        N = math.ceil(n / max_lpp)
        # Distribute as evenly as possible (first pages get one extra line).
        base, extra = divmod(n, N)
        pages_lines, idx = [], 0
        for i in range(N):
            count = base + (1 if i < extra else 0)
            pages_lines.append(lines[idx : idx + count])
            idx += count

    # Draw — each page's lines are spread to fill its full height,
    # but capped at max_leading * size to avoid absurd gaps with few lines.
    c = canvas.Canvas(output_path, pagesize=(pw, ph))
    for page_lines in pages_lines:
        line_height = min(uh / len(page_lines), size * max_leading)
        c.setFont(font, size)
        y = ph - top - size
        for line in page_lines:
            c.drawString(left, y, line)
            y -= line_height
        c.showPage()
    c.save()

    return dict(
        size=size,
        lines=n,
        pages=len(pages_lines),
        paper=paper,
        landscape=landscape,
        margins=margins,
    )


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Fit text onto the fewest PDF pages at the largest readable font size."
    )
    ap.add_argument("input", help="Input .txt file")
    ap.add_argument(
        "output",
        nargs="?",
        help="Output .pdf file (default: input with .pdf extension)",
    )
    ap.add_argument("--paper", default="A4", choices=PAPER)
    ap.add_argument("--landscape", action="store_true")
    ap.add_argument(
        "--margins",
        default="36,36,36,36",
        help='Points: top,right,bottom,left (default 36=0.5")',
    )
    ap.add_argument("--font", default="Courier")
    ap.add_argument("--tabsize", type=int, default=8)
    ap.add_argument(
        "--min-size",
        type=int,
        default=10,
        dest="min_size",
        help="Minimum font size in pt (default 8); may produce >1 page",
    )
    ap.add_argument(
        "--max-size",
        type=int,
        default=None,
        dest="max_size",
        help="Maximum font size in pt (no cap by default)",
    )
    ap.add_argument(
        "--max-leading",
        type=float,
        default=MAX_LEADING_FACTOR,
        dest="max_leading",
        help=f"Max line spacing as a multiple of font size (default {MAX_LEADING_FACTOR})",
    )
    args = ap.parse_args()

    import os

    output = args.output or os.path.splitext(args.input)[0] + ".pdf"

    try:
        margins = tuple(int(x) for x in args.margins.split(","))
        if len(margins) != 4:
            raise ValueError
    except Exception:
        sys.exit("margins must be 'top,right,bottom,left' in points.")

    result = fit_text(
        args.input,
        output,
        paper=args.paper,
        landscape=args.landscape,
        margins=margins,
        font=args.font,
        tabsize=args.tabsize,
        min_size=args.min_size,
        max_size=args.max_size,
        max_leading=args.max_leading,
    )
    top, right, bottom, left = result["margins"]
    orient = "landscape" if result["landscape"] else "portrait"
    pages = result["pages"]
    suffix = f" [{pages} pages]" if pages > 1 else ""
    print(
        f"[OK] '{output}' — {result['size']}pt, {result['lines']} lines, "
        f"{result['paper']} {orient}, margins={top},{right},{bottom},{left}{suffix}"
    )
