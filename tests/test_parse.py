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


# ---------------------------------------------------------------- regressions from the first live eCFR run

def _section(body: str, ident: str = "1026.43"):
    xml = f'<DIV8 N="{ident}" TYPE="SECTION"><HEAD>§ {ident} Test.</HEAD>{body}</DIV8>'
    return parse_unit(xml, ident, "section")[2]


def test_defined_term_stays_in_body():
    # Live 1026.43(b): the italic run is the defined term, not a heading to strip.
    blocks = _section(
        "<P>(b) <I>Definitions.</I> For purposes of this section:</P>"
        "<P>(1) <I>Covered transaction</I> means a consumer credit transaction secured by a dwelling.</P>"
        "<P>(2) <I>Fully amortizing payment</I> means a periodic payment of principal and interest.</P>"
    )
    b1 = next(b for b in blocks if b.citation == "1026.43(b)(1)")
    assert b1.text.startswith("Covered transaction means")
    assert b1.heading == "Covered transaction"
    assert next(b for b in blocks if b.citation == "1026.43(b)").heading == "Definitions"


def test_ascii_hyphen_heading_dash_in_older_versions():
    # Pre-2022 eCFR versions write 'Payment calculation - (i)' where current XML has an em dash.
    old = _section("<P>(c) <I>Repayment ability</I> - (5) <I>Payment calculation</I> - (i) "
                   "<I>General rule.</I> A creditor must make the calculation.</P>"
                   "<P>(A) The fully indexed rate.</P>")
    new = _section("<P>(c) <I>Repayment ability</I>—(5) <I>Payment calculation</I>—(i) "
                   "<I>General rule.</I> A creditor must make the calculation.</P>"
                   "<P>(A) The fully indexed rate.</P>")
    assert [(b.citation, b.text) for b in old] == [(b.citation, b.text) for b in new]
    assert [b.citation for b in old] == ["1026.43(c)(5)(i)", "1026.43(c)(5)(i)(A)"]


def test_hyphen_inside_body_is_not_a_heading_dash():
    blocks = _section("<P>(a) <I>Scope.</I> This applies to a non-recourse loan.</P>")
    assert blocks[0].text == "This applies to a non-recourse loan."
