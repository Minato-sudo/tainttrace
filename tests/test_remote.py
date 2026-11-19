from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tainttrace.integrity import IntegrityChecker, RemoteSources, Status


def crossref_work(doi: str, updates: list[dict]) -> dict:
    return {"message": {"DOI": doi, "title": ["Some paper"], "updated-by": updates}}


def update(kind: str, source: str, y: int, m: int, d: int) -> dict:
    return {"type": kind, "source": source, "DOI": "10.1234/notice", "updated": {"date-parts": [[y, m, d]]}}


class FakeApis:
    """Routes Crossref and OpenAlex requests to canned answers and counts calls."""

    def __init__(self, crossref: dict | None = None, openalex: dict | None = None, fail: bool = False):
        self.crossref = crossref or {}
        self.openalex = openalex or {}
        self.fail = fail
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if request.url.host == "api.crossref.org":
            doi = path.removeprefix("/works/")
            if doi in self.crossref:
                return httpx.Response(200, json=self.crossref[doi])
        if request.url.host == "api.openalex.org":
            doi = path.removeprefix("/works/doi:")
            if doi in self.openalex:
                return httpx.Response(200, json=self.openalex[doi])
        return httpx.Response(404)


def make_checker(store, apis: FakeApis, **kwargs) -> IntegrityChecker:
    client = httpx.Client(transport=httpx.MockTransport(apis))
    return IntegrityChecker(store, RemoteSources(store, client=client, **kwargs))


def test_publisher_retraction_missing_from_rw(store):
    apis = FakeApis(
        crossref={"10.1155/2023/1": crossref_work("10.1155/2023/1", [update("retraction", "publisher", 2026, 11, 1)])},
        openalex={"10.1155/2023/1": {"is_retracted": False}},
    )
    result = make_checker(store, apis).check(doi="10.1155/2023/1")
    assert result.status is Status.RETRACTED
    assert result.sources == ("crossref",)
    assert result.notices[0].source == "crossref/publisher"


def test_openalex_flag_alone_counts(store):
    apis = FakeApis(openalex={"10.1234/oa": {"is_retracted": True}})
    result = make_checker(store, apis).check(doi="10.1234/oa")
    assert result.status is Status.RETRACTED
    assert result.sources == ("openalex",)


def test_crossref_echo_of_rw_is_not_double_counted(store):
    doi = "10.1000/orig600"  # a correction in the fixture, dated 2022-02-02
    apis = FakeApis(crossref={doi: crossref_work(doi, [update("correction", "retraction-watch", 2022, 2, 2)])})
    result = make_checker(store, apis).check(doi=doi)
    assert result.status is Status.CORRECTED
    assert len(result.notices) == 1


def test_crossref_adds_newer_retraction_to_local_concern(store):
    doi = "10.1000/orig600"
    apis = FakeApis(crossref={doi: crossref_work(doi, [update("retraction", "publisher", 2026, 1, 5)])})
    result = make_checker(store, apis).check(doi=doi)
    assert result.status is Status.RETRACTED
    assert set(result.sources) == {"retraction_watch", "crossref"}


def test_skips_network_when_already_retracted(store):
    apis = FakeApis()
    make_checker(store, apis).check(doi="10.1038/nature04533")
    assert apis.calls == 0


def test_network_failure_is_reported_not_hidden(store):
    apis = FakeApis(fail=True)
    result = make_checker(store, apis).check(doi="10.1234/unknown")
    assert result.status is Status.NO_NOTICE
    assert len(result.errors) == 2


def test_results_are_cached(store):
    apis = FakeApis(openalex={"10.1234/c": {"is_retracted": False}})
    checker = make_checker(store, apis)
    checker.check(doi="10.1234/c")
    first = apis.calls
    checker.check(doi="10.1234/c")
    assert apis.calls == first == 2


@pytest.mark.parametrize("age, refetch", [(timedelta(days=1), False), (timedelta(days=8), True)])
def test_cache_expires(store, age, refetch):
    stale = (datetime.now(UTC) - age).isoformat()
    for source in ("crossref", "openalex"):
        store.cache_put(source, "10.1234/old", {"found": False, "data": None, "fetched_at": stale})
    apis = FakeApis()
    make_checker(store, apis).check(doi="10.1234/old")
    assert (apis.calls > 0) is refetch
