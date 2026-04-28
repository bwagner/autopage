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
import re
import sys
import unicodedata
from dataclasses import dataclass

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen import canvas

PAPER = {"A4": A4, "LETTER": LETTER}


MAX_LEADING_FACTOR = 1.5
FONT_SIZE_SEARCH_MAX = 500
RULE_LINE_RE = re.compile(r"^\s*-{3,}\s*$")
RULE_LINE_WIDTH = 0.5


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


def _number_lines(raw_lines, start):
    """Return right-gutter labels parallel to the post-_extract_rules text lines.

    Each non-blank text line gets ``f"{group}.{n}"`` (n resets per group).
    Whitespace-only lines get ``None`` and don't advance the within-group
    counter. HLS lines bump the group lazily, so consecutive HLS collapse and
    a top-of-file HLS (with no preceding numbered line) does not advance.
    """
    labels = []
    group = start
    within = 0
    bumped_yet = False
    pending_bump = False
    for line in raw_lines:
        if RULE_LINE_RE.match(line):
            if bumped_yet:
                pending_bump = True
            continue
        if pending_bump:
            group += 1
            within = 0
            pending_bump = False
        if line.strip() == "":
            labels.append(None)
        else:
            within += 1
            labels.append(f"{group}.{within}")
            bumped_yet = True
    return labels


def _extract_rules(lines):
    """Strip rule-marker lines from input, recording their positions.

    Returns (text_lines, rule_positions) where each entry j of rule_positions
    means "draw a rule after text line j"; j == -1 means "before the first
    text line" (top rule). Consecutive markers collapse to one entry.
    """
    text_lines = []
    rule_positions = []
    last_was_rule = False
    for line in lines:
        if RULE_LINE_RE.match(line):
            if not last_was_rule:
                rule_positions.append(len(text_lines) - 1)
                last_was_rule = True
        else:
            text_lines.append(line)
            last_was_rule = False
    return text_lines, rule_positions


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


def _render(
    output_path,
    pages,
    page_size,
    margins,
    font,
    size,
    usable_height,
    max_leading,
    rule_positions=(),
    labels=None,
):
    top, right, bottom, left = margins
    pw, ph = page_size
    rule_x_end = pw - right
    rule_set = set(rule_positions)
    ascent, descent = getAscentDescent(font, size)
    c = canvas.Canvas(output_path, pagesize=(pw, ph))
    global_idx = 0
    for page_idx, page_lines in enumerate(pages):
        # Spread lines to fill height, but cap leading to avoid absurd gaps.
        line_height = min(usable_height / len(page_lines), size * max_leading)
        # Centre the rule in the visual gap between glyph extents (descender
        # of upper line ↔ ascender of lower line), not between baselines.
        # descent is negative; this is positive at typical leadings.
        inline_rule_offset = (line_height - ascent - descent) / 2
        c.setFont(font, size)
        c.setLineWidth(RULE_LINE_WIDTH)
        if page_idx == 0 and -1 in rule_set:
            c.line(left, ph - top, rule_x_end, ph - top)
        y = ph - top - size
        for i, line in enumerate(page_lines):
            c.drawString(left, y, line)
            if labels is not None:
                label = labels[global_idx + i]
                if label is not None:
                    c.drawRightString(rule_x_end, y, label)
            if (global_idx + i) in rule_set:
                if i < len(page_lines) - 1:
                    rule_y = y - inline_rule_offset
                else:
                    rule_y = bottom
                c.line(left, rule_y, rule_x_end, rule_y)
            y -= line_height
        c.showPage()
        global_idx += len(page_lines)
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
    number=False,
    start_group=1,
):
    top, right, bottom, left = margins
    pw, ph = PAPER[paper]
    if landscape:
        pw, ph = ph, pw
    uw, uh = pw - left - right, ph - top - bottom

    raw_lines = _load_lines(input_path, tabsize)
    lines, rule_positions = _extract_rules(raw_lines)
    lines = lines or [""]
    labels = _number_lines(raw_lines, start_group) if number else None
    if labels is not None and len(labels) < len(lines):
        labels = labels + [None] * (len(lines) - len(labels))
    width_size = _max_font_size_by_width(lines, font, uw, max_size)
    size, pages = _paginate(lines, width_size, uh, min_size)
    _render(
        output_path,
        pages,
        (pw, ph),
        margins,
        font,
        size,
        uh,
        max_leading,
        rule_positions=rule_positions,
        labels=labels,
    )

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
    ap.add_argument(
        "--number",
        "-n",
        action="store_true",
        dest="number",
        help="Number lines in the right gutter as G.N. Groups are delimited "
        "by horizontal-rule markers; blank lines are skipped.",
    )
    ap.add_argument(
        "--start-group",
        "-s",
        type=int,
        default=None,
        dest="start_group",
        help="Group number to start at (default 1). Implies --number.",
    )
    args = ap.parse_args(argv)
    if args.start_group is not None:
        args.number = True
    if args.start_group is None:
        args.start_group = 1

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
        number=args.number,
        start_group=args.start_group,
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
