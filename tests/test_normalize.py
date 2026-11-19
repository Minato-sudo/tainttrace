import pytest

from tainttrace.integrity.normalize import normalize_doi, normalize_pmid, normalize_title


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.1056/NEJMoa1200303", "10.1056/nejmoa1200303"),
        ("https://doi.org/10.1056/NEJMoa1200303", "10.1056/nejmoa1200303"),
        ("http://dx.doi.org/10.1038/nature04533", "10.1038/nature04533"),
        ("doi: 10.1038/nature04533.", "10.1038/nature04533"),
        ("  10.1155/2023/6455106 ", "10.1155/2023/6455106"),
        ("unavailable", None),
        ("", None),
        (None, None),
        ("nature04533", None),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("23432189", "23432189"), ("0", None), ("", None), (" 007 ", "7"), ("abc", None)],
)
def test_normalize_pmid(raw, expected):
    assert normalize_pmid(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("RETRACTED: Visfatin: a protein", "visfatin a protein"),
        ("RETRACTED ARTICLE: A specific amyloid-β assembly", "a specific amyloid β assembly"),
        ("Retraction: Retracted: Café   effects!", "cafe effects"),
        ("Withdrawn - Title here", "title here"),
        ("Retractions in the literature", "retractions in the literature"),
        (None, ""),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected
