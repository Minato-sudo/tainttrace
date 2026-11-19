"""Data types for retraction and editorial-notice status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class NoticeType(StrEnum):
    """Kinds of editorial notice recorded by Retraction Watch."""

    RETRACTION = "retraction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    CORRECTION = "correction"
    REINSTATEMENT = "reinstatement"

    @classmethod
    def from_rw(cls, nature: str) -> NoticeType | None:
        return _RW_NATURE.get(nature.strip().lower())


_RW_NATURE = {
    "retraction": NoticeType.RETRACTION,
    "expression of concern": NoticeType.EXPRESSION_OF_CONCERN,
    "correction": NoticeType.CORRECTION,
    "reinstatement": NoticeType.REINSTATEMENT,
}


class Status(StrEnum):
    """Current integrity status of a paper, derived from all its notices.

    NO_NOTICE means no source we checked records a notice. It is not a
    guarantee that the paper is sound.
    """

    RETRACTED = "retracted"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATED = "reinstated"
    CORRECTED = "corrected"
    NO_NOTICE = "no_notice"

    @property
    def is_unreliable(self) -> bool:
        return self in (Status.RETRACTED, Status.EXPRESSION_OF_CONCERN)


@dataclass(frozen=True)
class Notice:
    """One editorial notice about one original paper."""

    record_id: str
    notice_type: NoticeType
    notice_date: date | None
    doi: str | None
    pmid: str | None
    title: str
    journal: str
    authors: str
    original_date: date | None
    notice_doi: str | None
    reasons: tuple[str, ...]
    source: str = "retraction_watch"


@dataclass(frozen=True)
class CheckResult:
    """Answer to "what is the integrity status of this paper?"."""

    status: Status
    matched_by: str | None
    """How the paper was identified: doi, pmid, notice_doi, title, or None."""
    sources: tuple[str, ...]
    """Which data sources contributed: retraction_watch, openalex, crossref."""
    notices: tuple[Notice, ...] = field(default_factory=tuple)
    title_score: float | None = None
    """Fuzzy title similarity (0-100) when matched_by == "title"."""
    is_notice_itself: bool = False
    """True when the identifier given was the DOI of a notice, not a paper."""
    errors: tuple[str, ...] = ()
    """Remote sources that could not be reached. Non-empty means the status
    may be incomplete, not that the paper is fine."""

    @property
    def certain(self) -> bool:
        """True when the paper was identified by an exact identifier.

        Title-only matches are probable, not certain: journals sometimes
        publish the same title twice (an accidental duplicate, a reissued
        version), and only one copy is retracted.
        """
        return self.matched_by in ("doi", "pmid", "notice_doi")

    @property
    def retraction_date(self) -> date | None:
        dates = [
            n.notice_date
            for n in self.notices
            if n.notice_type is NoticeType.RETRACTION and n.notice_date
        ]
        return max(dates) if dates else None

    @property
    def reasons(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for n in self.notices:
            for r in n.reasons:
                seen.setdefault(r, None)
        return tuple(seen)
