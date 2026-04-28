#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pytest>=8.0",
#     "reportlab",
#     "pypdf",
# ]
# ///
"""Tests for autopage.py."""

import sys
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

sys.path.insert(0, str(Path(__file__).parent.parent))
import autopage


def _extract_text(pdf_path):
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_roundtrip_ascii(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("hello\nworld\n", encoding="utf-8")
    out = tmp_path / "out.pdf"
    autopage.fit_text(str(src), str(out))
    text = _extract_text(out)
    assert "hello" in text
    assert "world" in text


def test_roundtrip_decomposed_umlaut_renders_as_nfc(tmp_path):
    # "a" + combining diaeresis U+0308 (NFD) — macOS filesystem style.
    src = tmp_path / "in.txt"
    src.write_text("verla\u0308ngert\n", encoding="utf-8")
    out = tmp_path / "out.pdf"
    autopage.fit_text(str(src), str(out))
    text = _extract_text(out)
    # Precomposed "ä" must appear; no stray combining mark left behind.
    assert "verl\u00e4ngert" in text
    assert "\u0308" not in text


def test_multipage_when_too_many_lines(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    out = tmp_path / "out.pdf"
    result = autopage.fit_text(str(src), str(out), min_size=10)
    assert result.pages > 1
    assert result.lines == 500


# --- _max_font_size_by_width ---


def test_max_font_size_empty_lines_returns_cap():
    # No lines means no width constraint — should hit max_size or default cap.
    assert autopage.FONT_SIZE_SEARCH_MAX == autopage._max_font_size_by_width(
        [], "Courier", usable_width=1000, max_size=None
    )
    assert autopage._max_font_size_by_width([], "Courier", 1000, max_size=42) == 42


def test_max_font_size_saturates_at_width():
    # Binary-search invariant: result fits, result+1 does not.
    lines = ["abcdefghijklmnopqrstuvwxyz"]
    uw = 200.0
    size = autopage._max_font_size_by_width(lines, "Courier", uw, max_size=None)
    assert stringWidth(lines[0], "Courier", size) <= uw
    assert stringWidth(lines[0], "Courier", size + 1) > uw


def test_max_font_size_monotonic_in_width():
    # Wider page → size at least as large.
    lines = ["some monospace text goes here"]
    narrow = autopage._max_font_size_by_width(lines, "Courier", 100.0, max_size=None)
    wide = autopage._max_font_size_by_width(lines, "Courier", 400.0, max_size=None)
    assert wide >= narrow


def test_max_font_size_honors_max_size_cap():
    # Even on an infinitely wide page, result must not exceed max_size.
    size = autopage._max_font_size_by_width(["x"], "Courier", 10_000.0, max_size=14)
    assert size == 14


# --- _paginate ---


def test_paginate_single_page_when_height_fits():
    lines = ["a", "b", "c"]
    size, pages = _paginate_unpack(lines, width_size=20, usable_height=1000, min_size=10)
    assert len(pages) == 1
    assert pages[0] == lines
    assert size >= 10


def test_paginate_multipage_when_single_page_font_below_min():
    # Tight height + many lines → single-page size < min_size → spill.
    n = 100
    lines = [f"line {i}" for i in range(n)]
    size, pages = _paginate_unpack(lines, width_size=30, usable_height=200, min_size=12)
    assert len(pages) > 1
    assert size >= 12
    assert sum(len(p) for p in pages) == n


def test_paginate_preserves_line_order_and_count():
    lines = [f"line {i}" for i in range(10)]
    _, pages = _paginate_unpack(lines, width_size=20, usable_height=50, min_size=15)
    flat = [ln for page in pages for ln in page]
    assert flat == lines


def test_paginate_even_distribution():
    # 10 lines across forced 3 pages → 4,3,3 (first page gets the extra).
    lines = [f"l{i}" for i in range(10)]
    # width_size=15, uh=30, min_size=15 → size=15, max_lpp=30/15=2, N=ceil(10/2)=5.
    # Force a known split by picking numbers: want 10 lines split across 3 pages.
    # max_lpp must be 4 → uh/size = 4 → with size=15, uh=60.
    _, pages = _paginate_unpack(lines, width_size=15, usable_height=60, min_size=15)
    assert [len(p) for p in pages] == [4, 3, 3]


def _paginate_unpack(lines, width_size, usable_height, min_size):
    return autopage._paginate(lines, width_size, usable_height, min_size)


# --- _extract_rules ---


def test_extract_rules_no_markers_unchanged():
    lines = ["alpha", "beta", "gamma"]
    text, rules = autopage._extract_rules(lines)
    assert text == lines
    assert rules == []


def test_extract_rules_top_marker_yields_minus_one():
    text, rules = autopage._extract_rules(["---", "alpha", "beta"])
    assert text == ["alpha", "beta"]
    assert rules == [-1]


def test_extract_rules_inline_marker_position():
    text, rules = autopage._extract_rules(["alpha", "---", "beta"])
    assert text == ["alpha", "beta"]
    assert rules == [0]


def test_extract_rules_bottom_marker_position():
    text, rules = autopage._extract_rules(["alpha", "beta", "---"])
    assert text == ["alpha", "beta"]
    assert rules == [1]


def test_extract_rules_consecutive_markers_collapse():
    text, rules = autopage._extract_rules(["alpha", "---", "---", "----", "beta"])
    assert text == ["alpha", "beta"]
    assert rules == [0]


def test_extract_rules_separated_markers_stay_separate():
    text, rules = autopage._extract_rules(["alpha", "---", "beta", "---", "gamma"])
    assert text == ["alpha", "beta", "gamma"]
    assert rules == [0, 1]


def test_extract_rules_pattern_requires_three_hyphens():
    # Single/double hyphens are not markers; whitespace around 3+ is allowed.
    text, rules = autopage._extract_rules(["-", "--", "a", "  ---  ", "b"])
    assert text == ["-", "--", "a", "b"]
    assert rules == [2]


def test_extract_rules_only_markers_yields_empty_text():
    text, rules = autopage._extract_rules(["---", "---"])
    assert text == []
    assert rules == [-1]


# --- markers don't change pagination math ---


def test_markers_preserve_page_and_line_counts(tmp_path):
    n = 30
    body = [f"line {i}" for i in range(n)]
    plain = tmp_path / "plain.txt"
    plain.write_text("\n".join(body), encoding="utf-8")
    marked = tmp_path / "marked.txt"
    # Sprinkle markers: top, middle, bottom, plus a consecutive pair.
    marked_lines = ["---", *body[:10], "---", "---", *body[10:20], *body[20:], "---"]
    marked.write_text("\n".join(marked_lines), encoding="utf-8")

    out_a = tmp_path / "a.pdf"
    out_b = tmp_path / "b.pdf"
    a = autopage.fit_text(str(plain), str(out_a))
    b = autopage.fit_text(str(marked), str(out_b))
    assert (b.size, b.lines, b.pages) == (a.size, a.lines, a.pages)


# --- _number_lines ---


def test_number_lines_no_markers_basic():
    assert autopage._number_lines(["foo", "bar", "baz"], start=1) == [
        "1.1",
        "1.2",
        "1.3",
    ]


def test_number_lines_one_marker_bumps_group():
    assert autopage._number_lines(["foo", "---", "bar"], start=1) == ["1.1", "2.1"]


def test_number_lines_custom_start():
    assert autopage._number_lines(["foo", "bar", "---", "baz"], start=5) == [
        "5.1",
        "5.2",
        "6.1",
    ]


def test_number_lines_top_marker_does_not_bump():
    assert autopage._number_lines(["---", "foo", "bar"], start=1) == ["1.1", "1.2"]


def test_number_lines_bottom_marker_no_effect():
    assert autopage._number_lines(["foo", "---"], start=1) == ["1.1"]


def test_number_lines_consecutive_markers_collapse():
    assert autopage._number_lines(["foo", "---", "---", "bar"], start=1) == [
        "1.1",
        "2.1",
    ]


def test_number_lines_blank_line_skipped_does_not_advance():
    assert autopage._number_lines(["foo", "", "bar"], start=1) == ["1.1", None, "1.2"]


def test_number_lines_whitespace_only_is_blank():
    assert autopage._number_lines(["foo", "   ", "bar"], start=1) == [
        "1.1",
        None,
        "1.2",
    ]


def test_number_lines_zero_start():
    assert autopage._number_lines(["foo", "---", "bar"], start=0) == ["0.1", "1.1"]


def test_number_lines_negative_start():
    assert autopage._number_lines(["foo", "---", "bar"], start=-3) == ["-3.1", "-2.1"]


def test_number_lines_indented_line_skipped_does_not_advance():
    assert autopage._number_lines(["foo", "  bar", "baz"], start=1) == [
        "1.1",
        None,
        "1.2",
    ]


def test_number_lines_indented_only_lines_get_no_labels():
    assert autopage._number_lines(["  foo", "  bar"], start=1) == [None, None]


def test_number_lines_indented_after_marker_no_bump():
    # Indented lines don't count as numbered output, so a top-of-file HLS
    # followed only by indented lines still doesn't bump the group.
    assert autopage._number_lines(["  intro", "---", "foo"], start=1) == [
        None,
        "1.1",
    ]


def test_number_lines_parallel_to_extracted_text():
    raw = ["alpha", "---", "beta", "", "gamma", "---", "delta"]
    text, _ = autopage._extract_rules(raw)
    labels = autopage._number_lines(raw, start=1)
    assert len(labels) == len(text)


# --- numbered PDF round-trip ---


def test_numbered_pdf_contains_labels(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("alpha\nbeta\n---\ngamma\n", encoding="utf-8")
    out = tmp_path / "out.pdf"
    autopage.fit_text(str(src), str(out), number=True, start_group=1)
    text = _extract_text(out)
    for token in ("alpha", "beta", "gamma", "1.1", "1.2", "2.1"):
        assert token in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
