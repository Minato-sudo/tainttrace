"""`IntegrityChecker.check()`: the integrity status of one paper."""

from __future__ import annotations

from rapidfuzz import fuzz

from .models import CheckResult, Notice, NoticeType, Status
from .normalize import normalize_doi, normalize_pmid, normalize_title
from .remote import RemoteSources
from .status import derive_status
from .store import NoticeStore

# Titles shorter than this many words must match exactly; short generic
# titles ("Editorial", "Correction") collide too easily.
MIN_FUZZY_WORDS = 4


def _dedupe(notices: list[Notice]) -> list[Notice]:
    """Drop the same notice reported by several sources.

    Keyed on type, date and paper, so Crossref's echo of a Retraction Watch
    record does not count as a second notice. Earlier entries win, so pass
    local notices first.
    """
    seen: set[tuple] = set()
    out = []
    for n in notices:
        key = (n.notice_type, n.notice_date, n.doi or n.pmid or n.title)
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _sources(notices: list[Notice]) -> list[str]:
    return list(dict.fromkeys(n.source.split("/")[0] for n in notices))


class IntegrityChecker:
    def __init__(
        self,
        store: NoticeStore,
        remote: RemoteSources | None = None,
        title_threshold: float = 90.0,
    ):
        self.store = store
        self.remote = remote
        self.title_threshold = title_threshold

    def check(
        self,
        doi: str | None = None,
        pmid: str | None = None,
        title: str | None = None,
    ) -> CheckResult:
        """Look a paper up by DOI, then PMID, then title.

        A DOI that is not in Retraction Watch is not followed by a title
        search against records that carry a different DOI, since a similar
        title on another DOI is a different paper.
        """
        doi_n = normalize_doi(doi)
        pmid_n = normalize_pmid(pmid)
        title_n = normalize_title(title)
        if not (doi_n or pmid_n or title_n):
            raise ValueError("Give a valid DOI, PMID or title.")

        notices: list[Notice] = []
        matched_by: str | None = None
        is_notice = False
        title_score: float | None = None

        if doi_n:
            notices = self.store.by_doi(doi_n)
            if notices:
                matched_by = "doi"
            else:
                about = self.store.by_notice_doi(doi_n)
                if about:
                    notices = self.store.siblings(about[0])
                    matched_by, is_notice = "notice_doi", True
        if not notices and pmid_n:
            notices = self.store.by_pmid(pmid_n)
            if notices:
                matched_by = "pmid"
        if not notices and title_n:
            best, title_score = self._match_title(title_n, doi_n)
            if best is not None:
                notices = self.store.siblings(best)
                matched_by = "title"

        sources = _sources(notices)
        errors: tuple[str, ...] = ()
        status = derive_status(notices)

        lookup_doi = doi_n or next((n.doi for n in notices if n.doi), None)
        if self.remote and lookup_doi and status is not Status.RETRACTED:
            answer = self.remote.lookup(lookup_doi)
            errors = answer.errors
            if answer.notices:
                notices = _dedupe(notices + list(answer.notices))
                matched_by = matched_by or "doi"
            if answer.openalex_retracted and not any(
                n.notice_type is NoticeType.RETRACTION for n in notices
            ):
                # OpenAlex flags it but gives no notice details.
                notices.append(
                    Notice(
                        record_id=f"openalex:{lookup_doi}",
                        notice_type=NoticeType.RETRACTION,
                        notice_date=None,
                        doi=lookup_doi,
                        pmid=None,
                        title=title or "",
                        journal="",
                        authors="",
                        original_date=None,
                        notice_doi=None,
                        reasons=(),
                        source="openalex",
                    )
                )
                matched_by = matched_by or "doi"
            status = derive_status(notices)
            sources = _sources(notices)

        return CheckResult(
            status=status,
            matched_by=matched_by,
            sources=tuple(sources),
            notices=tuple(notices),
            title_score=title_score,
            is_notice_itself=is_notice,
            errors=errors,
        )

    def _match_title(
        self, title_n: str, doi_n: str | None
    ) -> tuple[Notice | None, float | None]:
        exact_only = len(title_n.split()) < MIN_FUZZY_WORDS
        best: Notice | None = None
        best_score = 0.0
        for cand in self.store.title_candidates(title_n):
            if doi_n and cand.doi and cand.doi != doi_n:
                continue
            cand_n = normalize_title(cand.title)
            if exact_only:
                score = 100.0 if cand_n == title_n else 0.0
            else:
                score = max(fuzz.ratio(title_n, cand_n), fuzz.token_sort_ratio(title_n, cand_n))
            if score > best_score:
                best, best_score = cand, score
        if best is None or best_score < self.title_threshold:
            return None, (best_score or None)
        return best, best_score
