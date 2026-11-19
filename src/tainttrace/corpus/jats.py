"""Parse JATS full-text XML into sections, paragraphs and resolved citations.

Each paragraph keeps the ids of the references it cites, so later phases
can ask "does this passage rest on a retracted paper?".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..integrity.normalize import normalize_doi, normalize_pmid

# Sections that are not part of the scientific argument.
SKIP_SECTIONS = re.compile(
    r"supplementar|acknowledg|funding|author.{0,3}contribution|conflict|competing|"
    r"declaration|data availability|availability of data|abbreviation|ethic|consent|"
    r"publisher.?s note|footnote|additional information|disclosure",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")
# Text left behind when citation markers sit next to punctuation: "[ ]", "( , )".
_EMPTY_BRACKETS = re.compile(r"[\[(]\s*[,;–\-\s]*\s*[\])]")


@dataclass
class Reference:
    ref_id: str
    doi: str | None
    pmid: str | None
    title: str


@dataclass
class Paragraph:
    text: str
    cites: list[str] = field(default_factory=list)
    """Reference ids (keys of Paper.references) cited in this paragraph."""


@dataclass
class Section:
    title: str
    paragraphs: list[Paragraph]


@dataclass
class Paper:
    pmcid: str
    doi: str | None
    pmid: str | None
    title: str
    journal: str
    year: int | None
    abstract: list[Paragraph]
    sections: list[Section]
    references: dict[str, Reference]


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return _SPACES.sub(" ", "".join(el.itertext())).strip()


def _paragraph(p: ET.Element) -> Paragraph:
    cites = [
        rid
        for x in p.iter("xref")
        if x.get("ref-type") == "bibr"
        for rid in (x.get("rid") or "").split()
    ]
    # Drop nested tables, figures and formulas; keep inline text.
    for tag in ("table-wrap", "fig", "disp-formula", "table"):
        for parent in p.iter():
            for child in list(parent):
                if child.tag == tag:
                    parent.remove(child)
    text = _EMPTY_BRACKETS.sub("", _text(p))
    return Paragraph(text=_SPACES.sub(" ", text).strip(), cites=list(dict.fromkeys(cites)))


def _walk_sections(parent: ET.Element, prefix: str = "") -> list[Section]:
    out: list[Section] = []
    for sec in parent.findall("sec"):
        title = _text(sec.find("title")) or "Untitled"
        full = f"{prefix} › {title}" if prefix else title
        if SKIP_SECTIONS.search(title):
            continue
        paras = [_paragraph(p) for p in sec.findall("p")]
        paras = [p for p in paras if len(p.text.split()) >= 8]
        if paras:
            out.append(Section(title=full, paragraphs=paras))
        out.extend(_walk_sections(sec, full))
    return out


def _references(root: ET.Element) -> dict[str, Reference]:
    refs = {}
    for ref in root.iter("ref"):
        rid = ref.get("id")
        if not rid:
            continue
        ids = {pid.get("pub-id-type"): (pid.text or "").strip() for pid in ref.iter("pub-id")}
        refs[rid] = Reference(
            ref_id=rid,
            doi=normalize_doi(ids.get("doi")),
            pmid=normalize_pmid(ids.get("pmid")),
            title=_text(ref.find(".//article-title")),
        )
    return refs


def parse_jats(path: Path, pmcid: str | None = None) -> Paper | None:
    """Parse one article; None when the XML has no usable body text."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    meta = root.find(".//article-meta")
    if meta is None:
        return None
    ids = {a.get("pub-id-type"): (a.text or "").strip() for a in meta.findall("article-id")}

    abstract = []
    for ab in meta.findall("abstract"):
        if ab.get("abstract-type") in ("graphical", "teaser", "toc"):
            continue
        paras = ab.findall(".//p") or [ab]
        abstract += [_paragraph(p) for p in paras]
    abstract = [p for p in abstract if p.text]

    body = root.find("body")
    sections = _walk_sections(body) if body is not None else []
    if not sections and body is not None:
        # Some articles put paragraphs straight under <body>.
        paras = [_paragraph(p) for p in body.findall("p")]
        paras = [p for p in paras if len(p.text.split()) >= 8]
        if paras:
            sections = [Section(title="Main text", paragraphs=paras)]
    if not sections and not abstract:
        return None

    year_el = meta.find(".//pub-date/year")
    year = (year_el.text or "").strip() if year_el is not None else ""
    pmc = ids.get("pmcid") or ids.get("pmc") or pmcid or path.stem
    if pmc and not pmc.startswith("PMC"):
        pmc = f"PMC{pmc}"

    return Paper(
        pmcid=pmc,
        doi=normalize_doi(ids.get("doi")),
        pmid=normalize_pmid(ids.get("pmid")),
        title=_text(meta.find(".//title-group/article-title")),
        journal=_text(root.find(".//journal-meta//journal-title")),
        year=int(year) if year.isdigit() else None,
        abstract=abstract,
        sections=sections,
        references=_references(root),
    )
