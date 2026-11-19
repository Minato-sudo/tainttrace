"""Split a parsed paper into retrieval chunks.

A chunk is a run of consecutive paragraphs from one section, up to about
`max_words` words. Paragraphs stay whole (they are the unit Claude cites),
except very long ones, which are split at sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .jats import Paper, Paragraph

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


@dataclass
class Chunk:
    ord: int
    section: str
    paragraphs: list[Paragraph]

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.paragraphs)


def _words(p: Paragraph) -> int:
    return len(p.text.split())


def _split_long(p: Paragraph, max_words: int) -> list[Paragraph]:
    if _words(p) <= max_words:
        return [p]
    pieces: list[Paragraph] = []
    current: list[str] = []
    for sentence in _SENTENCE_END.split(p.text):
        if current and len(" ".join(current + [sentence]).split()) > max_words:
            pieces.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    # Citation ids can't be placed per sentence reliably; every piece keeps
    # the paragraph's citations so none are lost.
    return [Paragraph(text=t, cites=list(p.cites)) for t in pieces]


def chunk_paper(paper: Paper, max_words: int = 220) -> list[Chunk]:
    chunks: list[Chunk] = []
    blocks = [("Abstract", paper.abstract)] + [(s.title, s.paragraphs) for s in paper.sections]
    for section, paragraphs in blocks:
        current: list[Paragraph] = []
        size = 0
        for para in paragraphs:
            for piece in _split_long(para, max_words):
                n = _words(piece)
                if current and size + n > max_words:
                    chunks.append(Chunk(len(chunks), section, current))
                    current, size = [], 0
                current.append(piece)
                size += n
        if current:
            chunks.append(Chunk(len(chunks), section, current))
    return chunks
