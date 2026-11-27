from datetime import date
from pathlib import Path

from evals.harness import GoldenItem, citation_matches, evaluate
from ingest.chunk import assign_intervals, chunk_versions
from ingest.metadata import SectionVersion
from ingest.parse import parse_unit
from retrieval.bm25 import BM25Retriever
from retrieval.entitlements import build_filter

FIX = Path(__file__).parent / "fixtures"
D2017, D2021 = date(2017, 1, 3), date(2021, 3, 1)


def _version(name: str, start: date, end: date | None) -> SectionVersion:
    heading, kind, blocks = parse_unit((FIX / name).read_text(encoding="utf-8"), "1026.43", "section")
    return SectionVersion(title=12, part="1026", identifier="1026.43", heading=heading, kind=kind,
                          valid_from=start, valid_to=end, blocks=blocks,
                          amending_documents=["85 FR 86308"] if start == D2021 else [])


def _qm_chunks():
    return chunk_versions([
        _version("section_1026_43_v2019.xml", D2017, D2021),
        _version("section_1026_43_v2021.xml", D2021, None),
    ])


def test_intervals():
    assert assign_intervals([date(2020, 1, 1), date(2017, 1, 3)]) == [
        (date(2017, 1, 3), date(2020, 1, 1)),
        (date(2020, 1, 1), None),
    ]


def test_unchanged_paragraphs_are_compacted():
    chunks = _qm_chunks()
    e1 = [c for c in chunks if c.citation == "1026.43(e)(1)"]
    assert len(e1) == 1
    assert e1[0].valid_from == D2017 and e1[0].valid_to is None


def test_changed_paragraph_is_superseded():
    chunks = _qm_chunks()
    vi = sorted((c for c in chunks if c.citation == "1026.43(e)(2)(vi)"), key=lambda c: c.valid_from)
    old, new = vi
    assert "43 percent" in old.text and old.valid_to == D2021
    assert old.superseded_by == new.chunk_id
    assert "average prime offer rate" in new.text and new.valid_to is None
    assert new.amending_documents == ["85 FR 86308"]


def test_context_breadcrumb_carries_ancestor_headings():
    chunks = _qm_chunks()
    vi = next(c for c in chunks if c.citation == "1026.43(e)(2)(vi)")
    assert "Qualified mortgages" in vi.context
    assert "Qualified mortgage defined" in vi.context


def test_removed_section_closes_every_chunk():
    v1 = _version("section_1026_43_v2019.xml", D2017, D2021)
    gone = SectionVersion(title=12, part="1026", identifier="1026.43", heading="[Reserved]",
                          kind=v1.kind, valid_from=D2021, removed=True)
    chunks = chunk_versions([v1, gone])
    assert chunks and all(c.valid_to == D2021 for c in chunks)


def test_as_of_filter_returns_the_rule_in_force():
    r = BM25Retriever(_qm_chunks())
    q = "qualified mortgage debt to income ratio limit percent"
    before = r.search(q, build_filter("public", date(2019, 6, 1)), k=3)
    after = r.search(q, build_filter("public", date(2023, 6, 1)), k=3)
    vi_before = next(h.chunk for h in before if h.chunk.citation == "1026.43(e)(2)(vi)")
    assert "43 percent" in vi_before.text
    assert all("43 percent" not in h.chunk.text for h in after)


def test_citation_matching():
    assert citation_matches("1026.19(e)(1)", "1026.19(e)(1)(iii)")
    assert citation_matches("1026.19(e)(1)(iii)", "1026.19(e)(1)(iii) [part 2]")
    assert not citation_matches("1026.19(e)(1)(iii)", "1026.19(e)(1)")
    assert not citation_matches("1026.1", "1026.19(a)")


def test_harness_reports_stale_rate_ablation():
    r = BM25Retriever(_qm_chunks())
    item = GoldenItem(id="qm", question="debt to income ratio 43 percent qualified mortgage",
                      as_of=date(2023, 6, 1), expected_citations=["1026.43(e)(2)(vi)"])
    report = evaluate(r, [item], k=5)
    assert report["recall@5"] == 1.0
    assert report["stale_rate@5"] == 0.0
    assert report["stale_rate@5_without_asof_filter"] > 0.0
