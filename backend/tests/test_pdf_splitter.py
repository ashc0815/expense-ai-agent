"""pdf_splitter — split multi-page PDFs into per-page byte blobs."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from backend.pdf_splitter import SplitError, split


def _make_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_single_page_returns_one_item():
    pdf = _make_pdf(1)
    out = split(pdf)
    assert len(out) == 1
    assert PdfReader(io.BytesIO(out[0])).pages.__len__() == 1


def test_three_page_returns_three_items():
    pdf = _make_pdf(3)
    out = split(pdf)
    assert len(out) == 3
    for page_bytes in out:
        assert PdfReader(io.BytesIO(page_bytes)).pages.__len__() == 1


def test_garbage_raises_split_error():
    with pytest.raises(SplitError):
        split(b"this is not a pdf")


def test_non_pdf_bytes_raises_split_error():
    with pytest.raises(SplitError):
        split(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
