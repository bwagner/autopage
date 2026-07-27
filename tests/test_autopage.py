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


def test_max_font_size_with_gutter_label_reserves_space():
    # Reserving gutter must shrink the chosen size and leave room for the label.
    lines = ["abcdefghijklmnop"]
    uw = 200.0
    plain = autopage._max_font_size_by_width(lines, "Courier", uw, max_size=None)
    with_gutter = autopage._max_font_size_by_width(
        lines, "Courier", uw, max_size=None, gutter_label="99.9"
    )
    assert with_gutter <= plain
    gutter_w = stringWidth("99.9 ", "Courier", with_gutter)
    body_w = stringWidth(lines[0], "Courier", with_gutter)
    assert body_w <= uw - gutter_w


def test_max_font_size_empty_gutter_label_no_reservation():
    lines = ["abcdefghij"]
    a = autopage._max_font_size_by_width(lines, "Courier", 200.0, max_size=None)
    b = autopage._max_font_size_by_width(
        lines, "Courier", 200.0, max_size=None, gutter_label=""
    )
    assert a == b


def test_max_font_size_honors_max_size_cap():
    # Even on an infinitely wide page, result must not exceed max_size.
    size = autopage._max_font_size_by_width(["x"], "Courier", 10_000.0, max_size=14)
    assert size == 14


# --- _paginate ---


def test_paginate_single_page_when_height_fits():
    lines = ["a", "b", "c"]
    size, pages = _paginate_unpack(
        lines, width_size=20, usable_height=1000, min_size=10
    )
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


# --- _title ---


def test_title_is_first_nonblank_line():
    assert autopage._title(["alpha", "beta"]) == "alpha"


def test_title_skips_leading_blank_lines():
    assert autopage._title(["", "   ", "alpha"]) == "alpha"


def test_title_skips_leading_rule_marker():
    assert autopage._title(["---", "alpha"]) == "alpha"


def test_title_is_stripped():
    assert autopage._title(["   indented title   ", "beta"]) == "indented title"


def test_title_all_blank_input_is_empty():
    assert autopage._title(["", "   ", "---"]) == ""


# --- _truncate_to_width ---


def test_truncate_leaves_fitting_text_unchanged():
    text = "short"
    w = stringWidth(text, "Courier", autopage.HEADER_FONT_SIZE)
    assert (
        autopage._truncate_to_width(text, "Courier", autopage.HEADER_FONT_SIZE, w)
        == text
    )


def test_truncate_clips_and_marks_overlong_text():
    text = "a very long title that will not fit in the space available"
    max_width = stringWidth("a" * 20, "Courier", autopage.HEADER_FONT_SIZE)
    out = autopage._truncate_to_width(
        text, "Courier", autopage.HEADER_FONT_SIZE, max_width
    )
    assert out != text
    assert out.endswith(autopage.ELLIPSIS)
    assert stringWidth(out, "Courier", autopage.HEADER_FONT_SIZE) <= max_width


def test_truncate_returns_empty_when_nothing_fits():
    text = "anything"
    assert (
        autopage._truncate_to_width(text, "Courier", autopage.HEADER_FONT_SIZE, 0) == ""
    )


# --- _page_header ---


_HEADER_ARGS = ("Courier", autopage.HEADER_FONT_SIZE)
_DATE = "2026-05-09 14:23:45"


def test_page_header_orders_title_page_date():
    h = autopage._page_header("My Title", 1, 3, _DATE, *_HEADER_ARGS, max_width=10_000)
    assert "p. 1/3" in h
    assert h.index("My Title") < h.index("p. 1/3") < h.index(_DATE)


def test_page_header_joins_with_separator():
    h = autopage._page_header("T", 2, 7, _DATE, *_HEADER_ARGS, max_width=10_000)
    assert h == f"T{autopage.HEADER_SEP}p. 2/7{autopage.HEADER_SEP}{_DATE}"


def test_page_header_without_title_has_no_leading_separator():
    h = autopage._page_header("", 1, 1, _DATE, *_HEADER_ARGS, max_width=10_000)
    assert h == f"p. 1/1{autopage.HEADER_SEP}{_DATE}"


def test_page_header_truncates_only_the_title():
    title = "an extremely long document title that cannot possibly fit up there"
    tail = f"p. 1/1{autopage.HEADER_SEP}{_DATE}"
    max_width = stringWidth(
        f"xxxxxxxxxx{autopage.HEADER_SEP}{tail}", "Courier", autopage.HEADER_FONT_SIZE
    )
    h = autopage._page_header(title, 1, 1, _DATE, *_HEADER_ARGS, max_width=max_width)
    assert h.endswith(tail)
    assert not h.startswith(title)
    assert stringWidth(h, "Courier", autopage.HEADER_FONT_SIZE) <= max_width


def test_page_header_drops_title_when_tail_alone_barely_fits():
    tail = f"p. 1/1{autopage.HEADER_SEP}{_DATE}"
    max_width = stringWidth(tail, "Courier", autopage.HEADER_FONT_SIZE)
    h = autopage._page_header(
        "Some Title", 1, 1, _DATE, *_HEADER_ARGS, max_width=max_width
    )
    assert h == tail


# --- header in the top margin (end-to-end) ---


def _write_with_fixed_mtime(path, text):
    import os
    import time

    path.write_text(text, encoding="utf-8")
    # Pin mtime to a known instant so the formatted timestamp is deterministic.
    fixed = time.mktime((2026, 5, 9, 14, 23, 45, 0, 0, -1))
    os.utime(path, (fixed, fixed))


def test_header_does_not_perturb_layout(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("\n".join(f"line {i}" for i in range(20)), encoding="utf-8")
    out_on = tmp_path / "on.pdf"
    out_off = tmp_path / "off.pdf"
    a = autopage.fit_text(str(src), str(out_on), header=True)
    b = autopage.fit_text(str(src), str(out_off), header=False)
    assert (b.size, b.lines, b.pages) == (a.size, a.lines, a.pages)


def test_header_appears_in_pdf(tmp_path):
    src = tmp_path / "in.txt"
    _write_with_fixed_mtime(src, "My Document Title\nbody line\n")
    out = tmp_path / "out.pdf"
    autopage.fit_text(str(src), str(out), header=True)
    text = _extract_text(out)
    assert "My Document Title" in text
    assert "p. 1/1" in text
    assert _DATE in text


def test_header_page_numbers_count_up_to_total(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    out = tmp_path / "out.pdf"
    result = autopage.fit_text(str(src), str(out), min_size=10, header=True)
    assert result.pages > 1
    text = _extract_text(out)
    assert f"p. 1/{result.pages}" in text
    assert f"p. 2/{result.pages}" in text


def test_no_header_suppresses_the_whole_line(tmp_path):
    src = tmp_path / "in.txt"
    _write_with_fixed_mtime(src, "My Document Title\nbody line\n")
    out = tmp_path / "out.pdf"
    autopage.fit_text(str(src), str(out), header=False)
    text = _extract_text(out)
    assert "body line" in text  # body still rendered
    assert "p. 1/" not in text
    assert _DATE not in text


# --- CLI option shape ---


def _optional_actions():
    """Every non-positional action of the real CLI parser, help excluded."""
    return [
        a
        for a in autopage._build_parser()._actions
        if a.option_strings and "--help" not in a.option_strings
    ]


def _shorts(action):
    return [s for s in action.option_strings if len(s) == 2 and s[0] == "-"]


def test_every_optional_flag_has_a_short_form():
    missing = [a.option_strings for a in _optional_actions() if not _shorts(a)]
    assert missing == []


def test_short_flags_are_unique():
    all_shorts = [s for a in _optional_actions() for s in _shorts(a)]
    assert len(all_shorts) == len(set(all_shorts))


def test_short_flags_do_not_collide_with_help():
    all_shorts = {s for a in _optional_actions() for s in _shorts(a)}
    assert "-h" not in all_shorts


def test_list_fonts_exits_cleanly_without_an_input_file(capsys):
    with pytest.raises(SystemExit) as exc:
        autopage.main(["--list-fonts"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for font in ("Courier", "Helvetica", "Times-Roman"):
        assert font in out


def test_list_fonts_short_flag_matches_long(capsys):
    with pytest.raises(SystemExit):
        autopage.main(["--list-fonts"])
    long_out = capsys.readouterr().out
    assert "Courier" in long_out  # guard against comparing two empty strings
    with pytest.raises(SystemExit):
        autopage.main(["-F"])
    assert capsys.readouterr().out == long_out


def test_list_fonts_marks_monospace_families(capsys):
    with pytest.raises(SystemExit):
        autopage.main(["--list-fonts"])
    lines = capsys.readouterr().out.splitlines()
    by_font = {ln.split()[0]: ln for ln in lines if ln.strip()}
    assert autopage.MONOSPACE_MARKER in by_font["Courier"]
    assert autopage.MONOSPACE_MARKER not in by_font["Helvetica"]
    assert autopage.MONOSPACE_MARKER not in by_font["Times-Roman"]


def test_is_monospace_measures_rather_than_guesses():
    assert autopage._is_monospace("Courier")
    assert autopage._is_monospace("Courier-Bold")
    assert not autopage._is_monospace("Helvetica")
    assert not autopage._is_monospace("Times-Roman")
    # Symbol shares widths for a few chars but is not fixed-pitch overall.
    assert not autopage._is_monospace("Symbol")


def test_available_fonts_includes_the_standard_base_set():
    fonts = autopage._available_fonts()
    assert fonts == sorted(fonts)
    for font in ("Courier", "Helvetica", "Times-Roman", "Symbol", "ZapfDingbats"):
        assert font in fonts


def test_short_flags_drive_the_same_options(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("alpha\nbeta\n", encoding="utf-8")
    out_short = tmp_path / "short.pdf"
    out_long = tmp_path / "long.pdf"
    common = ["-p", "LETTER", "-l", "-m", "20,20,20,20", "-f", "Courier"]
    autopage.main([str(src), str(out_short), *common, "-t", "4", "-i", "8", "-s", "3"])
    autopage.main(
        [
            str(src),
            str(out_long),
            "--paper",
            "LETTER",
            "--landscape",
            "--margins",
            "20,20,20,20",
            "--font",
            "Courier",
            "--tabsize",
            "4",
            "--min-size",
            "8",
            "--start-group",
            "3",
        ]
    )
    assert _extract_text(out_short) == _extract_text(out_long)
    assert "3.1" in _extract_text(out_short)  # -s 3 implied --number


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
