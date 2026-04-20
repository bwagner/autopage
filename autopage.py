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
  --font Courier            (reportlab-registered monospace font name)
  --tabsize 8               (tab expansion width)
  --min-size 10             (minimum font size in pt; may produce >1 page)
  --max-size N              (cap font size in pt)
"""

import argparse
import math
import os
import sys
import unicodedata
from dataclasses import dataclass

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAPER = {"A4": A4, "LETTER": LETTER}


MAX_LEADING_FACTOR = 1.5
FONT_SIZE_SEARCH_MAX = 500


@dataclass(frozen=True)
class FitResult:
    size: int
    lines: int
    pages: int
    paper: str
    landscape: bool
    margins: tuple


def _load_lines(input_path, tabsize):
    with open(input_path, encoding="utf-8") as f:
        raw = f.read()
    # NFC: combine decomposed marks (e.g. "a"+U+0308) into precomposed chars
    # (ä) so Type 1 WinAnsi fonts like Courier can render them.
    lines = unicodedata.normalize("NFC", raw).expandtabs(tabsize).splitlines()
    return lines or [""]


def _max_font_size_by_width(lines, font, usable_width, max_size):
    lo, hi = 1, max_size or FONT_SIZE_SEARCH_MAX
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if max((stringWidth(ln, font, mid) for ln in lines), default=0) <= usable_width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _paginate(lines, width_size, usable_height, min_size):
    """Return (final font size, pages) given the width-fit size and height budget."""
    n = len(lines)
    size_1page = min(width_size, int(usable_height / n))
    if size_1page >= min_size:
        return size_1page, [lines]
    # Multi-page mode: maximise font (width-only), distribute evenly.
    size = max(min_size, width_size)
    max_lpp = max(1, int(usable_height / size))
    num_pages = math.ceil(n / max_lpp)
    base, extra = divmod(n, num_pages)
    pages, idx = [], 0
    for i in range(num_pages):
        count = base + (1 if i < extra else 0)
        pages.append(lines[idx : idx + count])
        idx += count
    return size, pages


def _render(output_path, pages, page_size, margins, font, size, usable_height, max_leading):
    top, _right, _bottom, left = margins
    pw, ph = page_size
    c = canvas.Canvas(output_path, pagesize=(pw, ph))
    for page_lines in pages:
        # Spread lines to fill height, but cap leading to avoid absurd gaps.
        line_height = min(usable_height / len(page_lines), size * max_leading)
        c.setFont(font, size)
        y = ph - top - size
        for line in page_lines:
            c.drawString(left, y, line)
            y -= line_height
        c.showPage()
    c.save()


def fit_text(
    input_path,
    output_path,
    paper="A4",
    landscape=False,
    margins=(36, 36, 36, 36),
    font="Courier",  # reportlab-registered font name, not a file path
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

    lines = _load_lines(input_path, tabsize)
    width_size = _max_font_size_by_width(lines, font, uw, max_size)
    size, pages = _paginate(lines, width_size, uh, min_size)
    _render(output_path, pages, (pw, ph), margins, font, size, uh, max_leading)

    return FitResult(
        size=size,
        lines=len(lines),
        pages=len(pages),
        paper=paper,
        landscape=landscape,
        margins=margins,
    )


def _parse_margins(spec):
    parts = tuple(int(x) for x in spec.split(","))
    if len(parts) != 4:
        raise ValueError("margins must have four comma-separated integers")
    return parts


def main(argv=None):
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
        help="Minimum font size in pt (default 10); may produce >1 page",
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
    args = ap.parse_args(argv)

    output = args.output or os.path.splitext(args.input)[0] + ".pdf"

    try:
        margins = _parse_margins(args.margins)
    except ValueError:
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
    top, right, bottom, left = result.margins
    orient = "landscape" if result.landscape else "portrait"
    suffix = f" [{result.pages} pages]" if result.pages > 1 else ""
    print(
        f"[OK] '{output}' — {result.size}pt, {result.lines} lines, "
        f"{result.paper} {orient}, margins={top},{right},{bottom},{left}{suffix}"
    )


if __name__ == "__main__":
    main()
