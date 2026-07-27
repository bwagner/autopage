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
  --paper / -p A4|LETTER    (default: A4)
  --landscape / -l          (make it landscape)
  --margins / -m 36,36,36,36 (points: top,right,bottom,left; default 36=0.5")
  --font / -f Courier       (reportlab-registered monospace font name)
  --list-fonts / -F         (print the usable fonts, monospace marked, and exit)
  --tabsize / -t 8          (tab expansion width)
  --min-size / -i 10        (minimum font size in pt; may produce >1 page)
  --max-size / -x N         (cap font size in pt)
  --max-leading / -L F      (max line spacing as a multiple of font size)
  --number / -n             (number lines as G.N in the right gutter)
  --no-header               (suppress the top-margin "title  p. N/M  mtime" line)
"""

import argparse
import math
import os
import re
import string
import sys
import time
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
HEADER_FONT_SIZE = 6
HEADER_TOP_PAD = 2
HEADER_SEP = "  "
PAGE_LABEL_FMT = "p. {page}/{total}"
ELLIPSIS = "..."
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MONOSPACE_MARKER = "(monospace)"
FONT_PROBE_SIZE = 12
FONT_PROBE_CHARS = string.printable.strip()


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

    A label is ``f"{group}.{n}"`` (n resets per group). Skipped (no label, no
    counter advance) are: whitespace-only lines and indented lines (treated as
    continuations of the previous numbered line). HLS lines bump the group
    lazily, so consecutive HLS collapse and a top-of-file HLS (with no
    preceding numbered line) does not advance.

    Caller contract: ``raw_lines`` is the post-``expandtabs`` output of
    ``_load_lines``, so leading whitespace is always spaces here.
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
        if line.strip() == "" or line.startswith(" "):
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


def _title(lines):
    """Return the document title: first non-blank, non-rule line, stripped.

    Empty string when the input holds no such line.
    """
    for line in lines:
        if RULE_LINE_RE.match(line) or line.strip() == "":
            continue
        return line.strip()
    return ""


def _truncate_to_width(text, font, size, max_width):
    """Clip text to max_width, marking the cut with ELLIPSIS.

    Returns text unchanged when it already fits, or "" when not even one
    character plus the ellipsis does.
    """
    if stringWidth(text, font, size) <= max_width:
        return text
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end] + ELLIPSIS
        if stringWidth(candidate, font, size) <= max_width:
            return candidate
    return ""


def _page_header(title, page, total, date_str, font, size, max_width):
    """Build the top-margin header: ``<title>  p. N/M  <date>``.

    Only the title is truncated to honour max_width; the page label and date
    are never clipped. The title is dropped entirely when no part of it fits.
    """
    tail = HEADER_SEP.join((PAGE_LABEL_FMT.format(page=page, total=total), date_str))
    if not title:
        return tail
    room = max_width - stringWidth(HEADER_SEP + tail, font, size)
    shown = _truncate_to_width(title, font, size, room)
    return shown + HEADER_SEP + tail if shown else tail


def _max_font_size_by_width(lines, font, usable_width, max_size, gutter_label=""):
    lo, hi = 1, max_size or FONT_SIZE_SEARCH_MAX
    # Trailing space adds one-char padding between body text and gutter label.
    gutter_str = gutter_label + " " if gutter_label else ""
    while lo < hi:
        mid = (lo + hi + 1) // 2
        gutter = stringWidth(gutter_str, font, mid) if gutter_str else 0
        widest = max((stringWidth(ln, font, mid) for ln in lines), default=0)
        if widest <= usable_width - gutter:
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
    title="",
    date_str=None,
):
    top, right, bottom, left = margins
    pw, ph = page_size
    rule_x_end = pw - right
    header_width = pw - left - right
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
        if date_str is not None:
            # Drawn in the top margin (well above body top at ph - top), so it
            # never enters layout calculations.
            header = _page_header(
                title,
                page_idx + 1,
                len(pages),
                date_str,
                font,
                HEADER_FONT_SIZE,
                header_width,
            )
            c.setFont(font, HEADER_FONT_SIZE)
            c.drawRightString(
                rule_x_end, ph - HEADER_TOP_PAD - HEADER_FONT_SIZE, header
            )
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
    header=True,
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
    widest_label = max(
        (lab for lab in (labels or ()) if lab is not None), key=len, default=""
    )
    width_size = _max_font_size_by_width(
        lines, font, uw, max_size, gutter_label=widest_label
    )
    size, pages = _paginate(lines, width_size, uh, min_size)
    date_str = (
        time.strftime(DATE_FORMAT, time.localtime(os.path.getmtime(input_path)))
        if header
        else None
    )
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
        title=_title(lines),
        date_str=date_str,
    )

    return FitResult(
        size=size,
        lines=len(lines),
        pages=len(pages),
        paper=paper,
        landscape=landscape,
        margins=margins,
    )


def _available_fonts():
    """Sorted names of the fonts reportlab can use without registration.

    ``canvas.getAvailableFonts()`` is authoritative here;
    ``pdfmetrics.getRegisteredFontNames()`` lists only fonts already touched
    (just Symbol and ZapfDingbats on a fresh interpreter).
    """
    return sorted(canvas.Canvas(os.devnull).getAvailableFonts())


def _is_monospace(font):
    """True when every printable ASCII char has the same advance width."""
    widths = {stringWidth(ch, font, FONT_PROBE_SIZE) for ch in FONT_PROBE_CHARS}
    return len(widths) == 1


def _format_font_list():
    fonts = _available_fonts()
    pad = max(len(f) for f in fonts)
    return "\n".join(
        f"{f:<{pad}}  {MONOSPACE_MARKER}" if _is_monospace(f) else f for f in fonts
    )


class _ListFontsAction(argparse.Action):
    """Print the usable fonts and exit, like --help, before argv validation."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(_format_font_list())
        parser.exit()


def _parse_margins(spec):
    parts = tuple(int(x) for x in spec.split(","))
    if len(parts) != 4:
        raise ValueError("margins must have four comma-separated integers")
    return parts


def _build_parser():
    ap = argparse.ArgumentParser(
        description="Fit text onto the fewest PDF pages at the largest readable font size."
    )
    ap.add_argument("input", help="Input .txt file")
    ap.add_argument(
        "output",
        nargs="?",
        help="Output .pdf file (default: input with .pdf extension)",
    )
    ap.add_argument("--paper", "-p", default="A4", choices=PAPER)
    ap.add_argument("--landscape", "-l", action="store_true")
    ap.add_argument(
        "--margins",
        "-m",
        default="36,36,36,36",
        help='Points: top,right,bottom,left (default 36=0.5")',
    )
    ap.add_argument("--font", "-f", default="Courier")
    ap.add_argument(
        "--list-fonts",
        "-F",
        action=_ListFontsAction,
        help="List the fonts usable with --font (monospace ones marked) and exit.",
    )
    ap.add_argument("--tabsize", "-t", type=int, default=8)
    ap.add_argument(
        "--min-size",
        "-i",
        type=int,
        default=10,
        dest="min_size",
        help="Minimum font size in pt (default 10); may produce >1 page",
    )
    ap.add_argument(
        "--max-size",
        "-x",
        type=int,
        default=None,
        dest="max_size",
        help="Maximum font size in pt (no cap by default)",
    )
    ap.add_argument(
        "--max-leading",
        "-L",
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
    ap.add_argument(
        "--header",
        "-H",
        default=True,
        action=argparse.BooleanOptionalAction,
        dest="header",
        help="Print '<title>  p. N/M  YYYY-MM-DD HH:MM:SS' in the top margin, "
        "where title is the first non-blank line and the date is the input "
        "file's mtime (default on; --no-header suppresses).",
    )
    return ap


def main(argv=None):
    args = _build_parser().parse_args(argv)
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
        header=args.header,
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
