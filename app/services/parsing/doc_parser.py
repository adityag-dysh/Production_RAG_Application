"""Policies/reports: PDF, DOCX, text/markdown. Heading-aware paragraph packing with overlap."""
import io
import re
from pathlib import Path

from app.services.parsing import Chunk

MAX_CHARS, OVERLAP_BLOCKS = 1800, 1
_HEADING = re.compile(r"^(#{1,6}\s+.+|\d+(\.\d+)*\.?\s+[A-Z].{0,80}|[A-Z][A-Z0-9 \-]{3,80})$")


def _extract(filename: str, data: bytes) -> list[tuple[str, str]]:
    """returns [(page_label, text)]"""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return [(f"p.{i + 1}", p.extract_text() or "") for i, p in enumerate(PdfReader(io.BytesIO(data)).pages)]
    if ext == ".docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:  # keep table rows intact on one line
            parts += [" | ".join(c.text.strip() for c in r.cells) for r in t.rows]
        return [("", "\n\n".join(parts))]
    return [("", data.decode("utf-8", errors="replace"))]


def parse(filename: str, data: bytes) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page, text in _extract(filename, data):
        heading, buf, size = "", [], 0

        def flush():
            nonlocal buf, size
            if buf:
                chunks.append(Chunk("\n\n".join(buf), " > ".join(x for x in (heading, page) if x)))
                buf = buf[-OVERLAP_BLOCKS:] if len(buf) > 1 else []
                size = sum(len(b) for b in buf)

        for block in (b.strip() for b in re.split(r"\n\s*\n", text)):
            if not block:
                continue
            if _HEADING.match(block.split("\n")[0]) and len(block) < 120:
                flush(); buf, size = [], 0
                heading = block.lstrip("# ").strip()
            while len(block) > MAX_CHARS:  # hard split oversized blocks
                buf.append(block[:MAX_CHARS]); block = block[MAX_CHARS:]; flush()
            buf.append(block); size += len(block)
            if size >= MAX_CHARS:
                flush()
        if buf:
            chunks.append(Chunk("\n\n".join(buf), " > ".join(x for x in (heading, page) if x)))
    # drop duplicate trailing overlap-only chunks
    return [c for c in chunks if c.text.strip()]
