from pathlib import Path

from ingest.markers import MarkerStack
from ingest.metadata import DocKind
from ingest.parse import parse_unit

FIX = Path(__file__).parent / "fixtures"


def _blocks(name, ident, kind="section"):
    return parse_unit((FIX / name).read_text(encoding="utf-8"), ident, kind)


def test_marker_stack_resolves_i_as_letter_after_h():
    s = MarkerStack()
    for tok in ["a", "b", "c", "d", "e", "f", "g", "h", "i"]:
        s.push(tok)
    assert s.path() == ["i"]


def test_marker_stack_resolves_i_as_roman_under_digit():
    s = MarkerStack()
    for tok in ["e", "1", "i"]:
        s.push(tok)
    assert s.path() == ["e", "1", "i"]


def test_marker_stack_v_after_iv_is_roman():
    s = MarkerStack()
    for tok in ["a", "1", "i", "ii", "iii", "iv", "v"]:
        s.push(tok)
    assert s.path() == ["a", "1", "v"]


def test_marker_stack_pops_back_up():
    s = MarkerStack()
    for tok in ["e", "1", "iii", "A", "B", "2"]:
        s.push(tok)
    assert s.path() == ["e", "2"]


def test_section_citations_and_headings():
    heading, kind, blocks = _blocks("section_1026_19.xml", "1026.19")
    assert kind is DocKind.REGULATION
    assert heading.startswith("§ 1026.19")
    cites = [b.citation for b in blocks]
    assert "1026.19(e)(1)(iii)" in cites
    assert "1026.19(f)(1)(ii)" in cites
    assert "1026.19(e)(1)(iii)(B)(1)" in cites  # italic numerals are level five
    timing = next(b for b in blocks if b.citation == "1026.19(e)(1)(iii)")
    assert timing.heading == "Timing"
    assert timing.text.startswith("Except as provided")


def test_ninth_letter_is_not_roman():
    _, _, blocks = _blocks("section_1026_19.xml", "1026.19")
    assert any(b.citation == "1026.19(i)" and "ninth lettered" in b.text for b in blocks)


def test_flush_paragraph_joins_previous_block():
    _, _, blocks = _blocks("section_1026_19.xml", "1026.19")
    b = next(b for b in blocks if b.citation == "1026.19(e)(1)(iii)(B)")
    assert "flush paragraph continues" in b.text


def test_table_kept_whole_as_markdown():
    _, _, blocks = _blocks("section_1026_19.xml", "1026.19")
    tables = [b for b in blocks if b.is_table]
    assert len(tables) == 1
    assert "| Loan amount | Threshold |" in tables[0].text
    assert "| Less than $100,000 | $3,000 |" in tables[0].text


def test_citation_note_is_skipped():
    _, _, blocks = _blocks("section_1026_19.xml", "1026.19")
    assert not any("78 FR 79730" in b.text for b in blocks)


def test_commentary_citations():
    _, kind, blocks = _blocks("supplement_i.xml", "Supplement I to Part 1026", "appendix")
    assert kind is DocKind.INTERPRETATION
    cites = [b.citation for b in blocks]
    assert cites == [
        "Comment 19-1",
        "Comment 19(e)(1)(iii)-1",
        "Comment 19(e)(1)(iii)-1.i",
        "Comment 19(e)(1)(iii)-1.i.A",
        "Comment 19(e)(1)(iii)-1.ii",
        "Comment 19(e)(1)(iii)-2",
    ]
    assert blocks[0].heading == "Certain Mortgage and Variable-Rate Transactions"
