"""Multi-page PDF splitter.

Given PDF bytes, return a list of single-page PDF byte blobs. Single-page
input returns a list of length 1. Non-PDF or corrupt input raises SplitError.
"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class SplitError(ValueError):
    """Raised when input bytes can't be parsed as a PDF."""


def split(file_bytes: bytes) -> list[bytes]:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = list(reader.pages)
    except (PdfReadError, OSError) as exc:
        raise SplitError(f"not a valid PDF: {exc}") from exc

    if not pages:
        raise SplitError("PDF has zero pages")

    out: list[bytes] = []
    for page in pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out
