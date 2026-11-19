"""Cited answers from retrieved chunks, using Claude's search_result blocks.

Each retrieved chunk is sent as one `search_result` block whose content is
one text block per paragraph, so Claude's citations point at exact
paragraphs. This is the plain baseline: nothing about retractions reaches
the model here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

from ..retrieval.hybrid import Retrieved

DEFAULT_MODEL = "claude-opus-5-5"
# Route a refused request to Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You answer questions about biomedical research using only the \
search results provided with each question. Each search result is a passage from \
a published paper.

Cite the passages that support each statement. If the search results do not \
contain enough evidence to answer, say what is missing rather than drawing on \
outside knowledge. Where passages disagree, say so. Write for a researcher: \
precise, concise, no filler."""

_SPACES = re.compile(r"\s+")


def source_for(r: Retrieved) -> str:
    c = r.chunk
    return f"https://doi.org/{c.doi}" if c.doi else f"https://europepmc.org/article/PMC/{c.pmcid}"


def search_result_blocks(retrieved: list[Retrieved]) -> list[dict[str, Any]]:
    blocks = []
    for r in retrieved:
        c = r.chunk
        year = f", {c.year}" if c.year else ""
        blocks.append(
            {
                "type": "search_result",
                "source": source_for(r),
                "title": f"{c.title} ({c.journal}{year}) - {c.section}",
                "content": [{"type": "text", "text": p["text"]} for p in c.paragraphs],
                "citations": {"enabled": True},
            }
        )
    return blocks


@dataclass(frozen=True)
class Citation:
    result_index: int
    """Index into the retrieved list (and the search_result blocks sent)."""
    chunk_id: int | None
    first_paragraph: int
    end_paragraph: int
    """Exclusive end, as in the API's end_block_index."""
    cited_text: str


@dataclass
class Segment:
    text: str
    citations: list[Citation] = field(default_factory=list)


@dataclass
class Answer:
    segments: list[Segment]
    stop_reason: str | None
    model: str
    """The model that produced the answer (differs from the request after a fallback)."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments).strip()

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    @property
    def citations(self) -> list[Citation]:
        return [c for s in self.segments for c in s.citations]


def parse_response(response: Any, retrieved: list[Retrieved]) -> Answer:
    segments: list[Segment] = []
    for block in response.content:
        if block.type != "text":
            continue  # thinking and fallback marker blocks
        cites = []
        for c in block.citations or []:
            if c.type != "search_result_location":
                continue
            idx = c.search_result_index
            chunk_id = retrieved[idx].chunk.chunk_id if 0 <= idx < len(retrieved) else None
            cites.append(Citation(idx, chunk_id, c.start_block_index, c.end_block_index, c.cited_text))
        segments.append(Segment(block.text, cites))
    usage = getattr(response, "usage", None)
    return Answer(
        segments=segments,
        stop_reason=response.stop_reason,
        model=response.model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


def citation_problems(answer: Answer, retrieved: list[Retrieved]) -> list[str]:
    """Check every citation points at paragraphs that were actually sent."""
    problems = []
    norm = lambda t: _SPACES.sub(" ", t).strip()  # noqa: E731
    for c in answer.citations:
        if not 0 <= c.result_index < len(retrieved):
            problems.append(f"citation to result {c.result_index}, only {len(retrieved)} sent")
            continue
        paras = retrieved[c.result_index].chunk.paragraphs
        if not 0 <= c.first_paragraph < c.end_paragraph <= len(paras):
            problems.append(
                f"result {c.result_index}: paragraphs [{c.first_paragraph}, {c.end_paragraph}) "
                f"out of range (has {len(paras)})"
            )
            continue
        expected = "".join(p["text"] for p in paras[c.first_paragraph : c.end_paragraph])
        if norm(c.cited_text) != norm(expected):
            problems.append(f"result {c.result_index}: cited text does not match the passage sent")
    return problems


def has_claude_credentials() -> bool:
    """True when the SDK can find credentials without prompting."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config = os.path.join(os.path.expanduser("~"), ".config", "anthropic")
    return os.path.isdir(config) and bool(os.listdir(config))


class AnswerGenerator:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        effort: str = "medium",
        max_tokens: int = 16000,
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("TAINTTRACE_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self.max_tokens = max_tokens

    def answer(self, question: str, retrieved: list[Retrieved]) -> Answer:
        if not retrieved:
            return Answer([Segment("No relevant passages were found in the corpus.")], "no_results", "")
        content = search_result_blocks(retrieved) + [{"type": "text", "text": question}]
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        return parse_response(response, retrieved)
