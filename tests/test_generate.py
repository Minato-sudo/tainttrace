from types import SimpleNamespace as NS

from tainttrace.corpus.store import ChunkRecord
from tainttrace.generate.answer import (
    FALLBACK_BETA,
    AnswerGenerator,
    citation_problems,
    parse_response,
    search_result_blocks,
)
from tainttrace.retrieval.hybrid import Retrieved


def retrieved(n: int = 2) -> list[Retrieved]:
    out = []
    for i in range(n):
        chunk = ChunkRecord(
            chunk_id=100 + i, paper_id=i, pmcid=f"PMC{i}", doi=f"10.1000/p{i}" if i == 0 else None,
            title=f"Paper {i}", journal="J Test", year=2020, section="Results",
            paragraphs=[{"text": f"Paper {i} paragraph A.", "cites": []},
                        {"text": f"Paper {i} paragraph B.", "cites": ["R1"]}],
        )
        out.append(Retrieved(chunk, 1.0, 0.1))
    return out


def cite(idx: int, start: int, end: int, text: str):
    return NS(type="search_result_location", search_result_index=idx,
              start_block_index=start, end_block_index=end, cited_text=text)


def response(blocks, stop_reason="end_turn", model="claude-opus-5-5"):
    return NS(content=blocks, stop_reason=stop_reason, model=model,
              usage=NS(input_tokens=1200, output_tokens=80))


def test_search_result_blocks():
    blocks = search_result_blocks(retrieved())
    assert blocks[0]["type"] == "search_result"
    assert blocks[0]["source"] == "https://doi.org/10.1000/p0"
    assert blocks[1]["source"] == "https://europepmc.org/article/PMC/PMC1"
    assert blocks[0]["title"] == "Paper 0 (J Test, 2020) - Results"
    assert blocks[0]["content"] == [
        {"type": "text", "text": "Paper 0 paragraph A."},
        {"type": "text", "text": "Paper 0 paragraph B."},
    ]
    assert all(b["citations"] == {"enabled": True} for b in blocks)


def test_parse_response_maps_citations():
    r = retrieved()
    resp = response([
        NS(type="thinking", thinking=""),
        NS(type="text", text="Intro. ", citations=None),
        NS(type="text", text="Paper 1 found B", citations=[cite(1, 1, 2, "Paper 1 paragraph B.")]),
        NS(type="text", text=".", citations=[]),
    ])
    answer = parse_response(resp, r)
    assert answer.text == "Intro. Paper 1 found B."
    (c,) = answer.citations
    assert (c.result_index, c.chunk_id, c.first_paragraph, c.end_paragraph) == (1, 101, 1, 2)
    assert answer.input_tokens == 1200
    assert citation_problems(answer, r) == []


def test_citation_problems_detected():
    r = retrieved()
    resp = response([NS(type="text", text="x", citations=[
        cite(5, 0, 1, "anything"),
        cite(0, 1, 3, "Paper 0 paragraph B."),
        cite(0, 0, 1, "Something Claude made up."),
    ])])
    problems = citation_problems(parse_response(resp, r), r)
    assert len(problems) == 3
    assert "only 2 sent" in problems[0]
    assert "out of range" in problems[1]
    assert "does not match" in problems[2]


def test_refusal_is_flagged():
    answer = parse_response(response([], stop_reason="refusal"), retrieved())
    assert answer.refused and answer.text == ""


class FakeClient:
    def __init__(self, reply):
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))
        self.reply = reply

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


def test_generator_request_shape():
    client = FakeClient(response([NS(type="text", text="ok", citations=None)]))
    gen = AnswerGenerator(client=client, model="claude-opus-5-5")
    answer = gen.answer("What does paper 0 show?", retrieved())
    (call,) = client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "medium"}
    content = call["messages"][0]["content"]
    assert [b["type"] for b in content] == ["search_result", "search_result", "text"]
    assert content[-1]["text"] == "What does paper 0 show?"
    assert answer.text == "ok"


def test_generator_skips_call_without_passages():
    client = FakeClient(None)
    answer = AnswerGenerator(client=client).answer("q", [])
    assert client.calls == [] and answer.stop_reason == "no_results"
