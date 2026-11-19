from pathlib import Path

import httpx
import pytest

from tainttrace.corpus.chunk import chunk_paper
from tainttrace.corpus.jats import Paragraph, Paper, Section, parse_jats
from tainttrace.corpus.ncbi import NcbiPmc
from tainttrace.corpus.store import CorpusStore

JATS = Path(__file__).parent / "fixtures" / "jats_sample.xml"


@pytest.fixture
def paper() -> Paper:
    p = parse_jats(JATS)
    assert p is not None
    return p


def test_metadata(paper):
    assert paper.pmcid == "PMC1234567"
    assert paper.doi == "10.1000/jto.2020.42"
    assert paper.pmid == "31234567"
    assert paper.year == 2020
    assert paper.journal == "Journal of Test Oncology"
    assert paper.title == "MicroRNA-99 suppresses tumour growth in a test carcinoma"


def test_graphical_abstract_skipped(paper):
    assert [p.text for p in paper.abstract] == [
        "We show that microRNA-99 suppresses proliferation of test carcinoma cells in vitro and in vivo."
    ]


def test_sections_nest_skip_and_filter(paper):
    titles = [s.title for s in paper.sections]
    assert titles == ["Introduction", "Results › miR-99 inhibits proliferation"]
    # "Short." is under 8 words and dropped.
    assert len(paper.sections[0].paragraphs) == 1


def test_citations_resolved(paper):
    intro = paper.sections[0].paragraphs[0]
    assert intro.cites == ["R1", "R2", "R3"]
    assert paper.references["R1"].doi == "10.1000/review.1"
    assert paper.references["R1"].pmid == "111"
    assert paper.references["R2"].doi == "10.1000/retracted.2"
    assert paper.references["R3"].doi is None


def test_tables_removed(paper):
    text = paper.sections[1].paragraphs[0].text
    assert "TABLE CELL" not in text
    assert text.startswith("Overexpression of miR-99")


def test_unparseable_xml(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<article><front>")
    assert parse_jats(bad) is None


def _paper_with(paragraph_words: list[int]) -> Paper:
    paras = [Paragraph(" ".join(["word"] * n) + ".", cites=[f"R{i}"]) for i, n in enumerate(paragraph_words)]
    return Paper("PMC1", None, None, "T", "J", 2020, [], [Section("Results", paras)], {})


def test_chunks_respect_size():
    chunks = chunk_paper(_paper_with([100, 100, 100, 30]), max_words=220)
    assert [len(c.paragraphs) for c in chunks] == [2, 2]
    assert all(len(c.text.split()) <= 220 for c in chunks)


def test_long_paragraph_split_keeps_citations():
    sentences = " ".join(f"Sentence number {i} has exactly seven words." for i in range(60))
    paper = Paper("PMC1", None, None, "T", "J", 2020, [],
                  [Section("Discussion", [Paragraph(sentences, cites=["R9"])])], {})
    chunks = chunk_paper(paper, max_words=100)
    assert len(chunks) > 1
    assert all(p.cites == ["R9"] for c in chunks for p in c.paragraphs)
    assert all(len(p.text.split()) <= 100 for c in chunks for p in c.paragraphs)


def test_abstract_comes_first(paper):
    chunks = chunk_paper(paper)
    assert chunks[0].section == "Abstract"
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_store_roundtrip_and_bm25(tmp_path, paper):
    store = CorpusStore(tmp_path / "corpus.sqlite")
    store.add_paper(paper, chunk_paper(paper), "test", epmc_retracted=False)
    stats = store.stats()
    assert stats["papers"] == 1 and stats["references_with_doi"] == 2

    hits = store.bm25("colony formation miR-99", 5)
    assert hits
    (chunk,) = store.get_chunks([hits[0][0]])
    assert "colony formation" in chunk.text
    assert chunk.paragraphs[0]["cites"] == ["R2"]
    assert chunk.doi == "10.1000/jto.2020.42"
    assert store.has_paper("PMC1234567")
    assert store.bm25("!!!", 5) == []


def test_ncbi_batch_writes_one_file_per_article(tmp_path):
    xml = JATS.read_text().split("?>", 1)[1]
    body = f"<pmc-articleset>{xml}</pmc-articleset>".encode()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=body)

    ncbi = NcbiPmc(client=httpx.Client(transport=httpx.MockTransport(handler)))
    ncbi.min_interval = 0
    found = ncbi.fetch_batch(["PMC1234567", "PMC7654321"], tmp_path)
    assert set(found) == {"PMC1234567"}  # the other id was not returned
    assert parse_jats(found["PMC1234567"]).pmcid == "PMC1234567"
    assert b"id=1234567%2C7654321" in calls[0].content

    ncbi.fetch_batch(["PMC1234567"], tmp_path)
    assert len(calls) == 1  # served from the cache
