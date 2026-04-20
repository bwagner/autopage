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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
