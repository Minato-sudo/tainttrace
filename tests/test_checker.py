from datetime import date

import pytest

from tainttrace.integrity import IntegrityChecker, NoticeStore, Status, build_store
from tainttrace.integrity.rw import read_rw_csv

from .conftest import FIXTURE_CSV


def test_ingest_skips_unknown_notice_types(store):
    assert store.meta()["notice_count"] == "8"
    assert store.by_doi("10.1000/orig700") == []


def test_reasons_lose_plus_prefix(store):
    (notice,) = store.by_doi("10.1056/nejmoa1200303")
    assert notice.reasons == ("Error in Analyses", "Concerns/Issues about Data")


def test_check_by_doi(checker):
    result = checker.check(doi="https://doi.org/10.1056/NEJMoa1200303")
    assert result.status is Status.RETRACTED
    assert result.matched_by == "doi"
    assert result.sources == ("retraction_watch",)
    assert result.retraction_date == date(2018, 6, 13)


def test_multi_notice_paper(checker):
    result = checker.check(doi="10.1038/nature04533")
    assert result.status is Status.RETRACTED
    assert len(result.notices) == 2
    assert "Manipulation of Images" in result.reasons


def test_check_by_pmid(checker):
    result = checker.check(pmid="23432189")
    assert result.status is Status.RETRACTED
    assert result.matched_by == "pmid"


def test_notice_doi_resolves_to_original(checker):
    result = checker.check(doi="10.1038/s41586-024-07691-8")
    assert result.is_notice_itself
    assert result.matched_by == "notice_doi"
    assert result.status is Status.RETRACTED
    assert all(n.doi == "10.1038/nature04533" for n in result.notices)


def test_reinstated(checker):
    assert checker.check(doi="10.1000/orig300").status is Status.REINSTATED


def test_corrected(checker):
    assert checker.check(doi="10.1000/orig600").status is Status.CORRECTED


def test_unknown_doi_is_no_notice(checker):
    result = checker.check(doi="10.1038/nature14539")
    assert result.status is Status.NO_NOTICE
    assert result.matched_by is None
    assert result.notices == ()


def test_title_match_strips_retraction_marker(checker):
    result = checker.check(title="RETRACTED: Primary prevention of cardiovascular disease with a Mediterranean diet.")
    assert result.status is Status.RETRACTED
    assert result.matched_by == "title"
    assert result.title_score == pytest.approx(100.0)


def test_title_match_tolerates_small_differences(checker):
    result = checker.check(title="A paper without any DOI about soil microbe and drought")
    assert result.status is Status.RETRACTED
    assert 90 <= result.title_score < 100


def test_unrelated_title_does_not_match(checker):
    result = checker.check(title="Deep learning for protein structure prediction at scale")
    assert result.status is Status.NO_NOTICE


def test_short_titles_need_exact_match(checker):
    assert checker.check(title="Editorial").status is Status.RETRACTED
    assert checker.check(title="Editorials").status is Status.NO_NOTICE


def test_title_ignored_when_doi_points_elsewhere(checker):
    # Same title as a retracted record, but a different DOI: another paper.
    result = checker.check(
        doi="10.9999/different",
        title="Primary Prevention of Cardiovascular Disease with a Mediterranean Diet",
    )
    assert result.status is Status.NO_NOTICE


def test_title_fallback_allowed_for_records_without_doi(checker):
    result = checker.check(
        doi="10.9999/some-doi",
        title="A paper without any DOI about soil microbes and drought",
    )
    assert result.status is Status.RETRACTED


def test_requires_an_identifier(checker):
    with pytest.raises(ValueError):
        checker.check(doi="not a doi")


def test_missing_store_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="tainttrace sync"):
        NoticeStore(tmp_path / "nope.sqlite")


def test_rebuild_keeps_remote_cache(db_path):
    store = NoticeStore(db_path)
    store.cache_put("crossref", "10.1234/x", {"found": False, "data": None, "fetched_at": "x"})
    store.close()

    build_store(read_rw_csv(FIXTURE_CSV), db_path, source_file=FIXTURE_CSV)

    store = NoticeStore(db_path)
    assert store.cache_get("crossref", "10.1234/x")["found"] is False
    assert IntegrityChecker(store).check(doi="10.1038/nature04533").status is Status.RETRACTED
    store.close()
