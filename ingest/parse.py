"""Structure-aware parser for eCFR XML.

Turns one section, appendix or commentary unit into a list of Blocks, each with a
precise citation. Three shapes are handled:

  * Regulation sections (DIV8): paragraphs keyed by the (a)(1)(i)(A) hierarchy.
  * Official interpretations (Supplement I): comments keyed like 'Comment 19(e)(1)(iii)-1.i'.
  * Other appendices (model forms, worksheets): blocks keyed by heading.

Tables are kept whole and rendered as Markdown, never split across chunks.
"""

from __future__ import annotations

import re

from lxml import etree

from ingest.markers import (
    EMPH,
    ITALIC,
    MarkerStack,
    clean_heading,
    split_leading_markers,
    strip_sentinels,
)
from ingest.metadata import Block, DocKind

TEXT_TAGS = {"P", "FP", "PSPACE"}
HEADING_TAGS = {"HD1", "HD2", "HD3", "HD4", "HD5", "HD6", "HED"}
SKIP_TAGS = {"CITA", "AUTH", "SOURCE", "EDNOTE", "SECAUTH", "FTREF", "PRTPAGE", "STARS"}
TABLE_TAGS = {"GPOTABLE", "TABLE"}
GRAPHIC_TAGS = {"GPH", "GID", "IMG", "img", "MATH"}


# ---------------------------------------------------------------- text helpers

def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def element_text(el: etree._Element) -> str:
    """Flatten an element to text, marking italics with sentinels for the marker parser."""
    raw = etree.tostring(el, encoding="unicode", with_tail=False)
    raw = re.sub(r"\(\s*<(I|E)(?:\s[^>]*)?>\s*([a-zA-Z]{1,5}|\d{1,3})\s*</\1>\s*\)", "(" + ITALIC + r"\2)", raw)
    raw = re.sub(r"<(I|E)(?:\s[^>]*)?>", EMPH, raw)
    raw = re.sub(r"</(I|E)>", EMPH, raw)
    frag = etree.fromstring(raw.encode("utf-8"), parser=etree.XMLParser(recover=True))
    text = "".join(frag.itertext()) if frag is not None else ""
    text = re.sub(EMPH + r"\s*" + EMPH, " ", text)  # adjacent italic runs merge
    return _norm_ws(text)


def table_to_markdown(el: etree._Element) -> str:
    rows: list[list[str]] = []
    if el.tag == "GPOTABLE":
        heads = [_norm_ws("".join(c.itertext())) for c in el.iter("CHED")]
        if heads:
            rows.append(heads)
        for row in el.iter("ROW"):
            rows.append([_norm_ws("".join(e.itertext())) for e in row.iter("ENT")])
    else:
        for tr in el.iter("TR", "tr"):
            rows.append([_norm_ws("".join(c.itertext())) for c in tr if c.tag in ("TH", "TD", "th", "td")])
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    title = el.findtext("TTITLE") or el.findtext("CAPTION") or el.findtext("caption")
    return (f"**{_norm_ws(title)}**\n\n" if title else "") + "\n".join(lines)


def find_unit(root: etree._Element, identifier: str) -> etree._Element:
    """Locate the DIV for this identifier inside the wrapper the API returns."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.get("N") == identifier and el.tag.startswith("DIV"):
            return el
    for tag in ("DIV8", "DIV9"):
        hit = next(root.iter(tag), None)
        if hit is not None:
            return hit
    return root


def unit_heading(unit: etree._Element) -> str:
    head = unit.find("HEAD")
    return _norm_ws("".join(head.itertext())) if head is not None else unit.get("N", "")


def classify(identifier: str, unit_type: str) -> DocKind:
    if identifier.lower().startswith("supplement"):
        return DocKind.INTERPRETATION
    if unit_type == "appendix" or identifier.lower().startswith("appendix"):
        return DocKind.APPENDIX
    return DocKind.REGULATION


def _walk(el: etree._Element):
    """Yield content-bearing elements in document order, not descending into tables."""
    for child in el:
        if not isinstance(child.tag, str) or child.tag in SKIP_TAGS or child.tag == "HEAD":
            continue
        if child.tag in TEXT_TAGS or child.tag in HEADING_TAGS or child.tag in TABLE_TAGS \
                or child.tag in GRAPHIC_TAGS:
            yield child
        else:
            yield from _walk(child)


# ---------------------------------------------------------------- regulation sections

def parse_section(unit: etree._Element, identifier: str) -> list[Block]:
    blocks: list[Block] = []
    stack = MarkerStack()
    headings: dict[tuple[str, ...], str] = {}

    def cite(path: list[str]) -> str:
        return identifier + "".join(f"({p})" for p in path)

    def ancestors(path: list[str]) -> list[tuple[str, str]]:
        out = []
        for i in range(1, len(path)):
            h = headings.get(tuple(path[:i]))
            if h:
                out.append((cite(path[:i]), h))
        return out

    def append_to_last(text: str, is_table: bool = False) -> None:
        if blocks and not (is_table or blocks[-1].is_table):
            blocks[-1].text = (blocks[-1].text + "\n" + text).strip()
        else:
            path = stack.path()
            blocks.append(Block(path=path, citation=cite(path), text=text, is_table=is_table,
                                heading=headings.get(tuple(path)), ancestors=ancestors(path)))

    for el in _walk(unit):
        if el.tag in TABLE_TAGS:
            md = table_to_markdown(el)
            if md:
                append_to_last(md, is_table=True)
            continue
        if el.tag in GRAPHIC_TAGS:
            continue
        if el.tag in HEADING_TAGS:
            text = strip_sentinels(element_text(el))
            if text:
                append_to_last(f"## {text}")
            continue

        text = element_text(el)
        if not text:
            continue
        markers = split_leading_markers(text) if el.tag == "P" else []
        accepted_end = 0
        last_heading = None
        for tok, italic, heading, _start, end in markers:
            if not stack.push(tok, italic):
                break
            accepted_end = end
            last_heading = heading
            if heading:
                headings[tuple(stack.path())] = heading
        if accepted_end == 0:
            append_to_last(strip_sentinels(text))
            continue
        body = strip_sentinels(text[accepted_end:]).strip()
        path = stack.path()
        blocks.append(Block(path=path, citation=cite(path), text=body,
                            heading=last_heading or headings.get(tuple(path)),
                            ancestors=ancestors(path)))

    # A heading-only paragraph like '(e) Mortgage loans—' carries no body; its children do.
    return [b for b in blocks if b.text or b.is_table]


# ---------------------------------------------------------------- Supplement I commentary

SECTION_HEAD_RE = re.compile(r"Section\s+(\d+)\.(\d+[a-z]?)")
PARA_HEAD_RE = re.compile(r"^(?:Paragraphs?\s+)?(\d+[a-z]?)((?:\([^)\s]{1,6}\))+)")
# 'Appendix K—Total Annual Loan Cost Rate ...', 'Appendices G and H—Open-End and Closed-End ...'
APPENDIX_HEAD_RE = re.compile(r"^Appendi(?:x|ces)\s+([A-Z](?:-\d+)?(?:\s+(?:and|through)\s+[A-Z](?:-\d+)?)?)(?=\W|$)")
# Paragraph headings inside appendix commentary: '(b)(9) Assumption for discretionary cash advances'
APPENDIX_PARA_RE = re.compile(r"^((?:\([^)\s]{1,6}\))+)(?=\s|$)")

# Comment levels, outermost first: 1. / i. / A. / italic 1. (e.g. comment 3(b)-4.iv.A.1)
DIGIT, ROMAN_L, UPPER, IT_DIGIT = 0, 1, 2, 3
COMMENT_RE = [
    (DIGIT, re.compile(r"^(\d{1,3})\.\s+")),
    (ROMAN_L, re.compile(r"^([ivx]{1,6})\.\s+")),
    (UPPER, re.compile(r"^([A-Z])\.\s+")),
]
# Italic numerals arrive wrapped in emphasis sentinels: <I>1.</I> -> '‡1.‡'
IT_DIGIT_RE = re.compile(r"^" + EMPH + r"(\d{1,2})\.\s*" + EMPH + r"\s*")


def _comment_marker(raw: str) -> tuple[int, str, str] | None:
    """(level, token, body) for a paragraph that opens a comment, else None.

    `raw` still carries the emphasis sentinels, which is the only way to tell the
    italic fourth level ('<I>1.</I>') from a new top-level comment ('1.').
    """
    m = IT_DIGIT_RE.match(raw)
    if m:
        return IT_DIGIT, m.group(1), raw[m.end():]
    for level, rx in COMMENT_RE:
        m = rx.match(raw)
        if m:
            return level, m.group(1), raw[m.end():]
    return None


FIRST_OF_LEVEL = {ROMAN_L: "i", UPPER: "A", IT_DIGIT: "1"}
LEAD_HEADING_RE = re.compile(EMPH + r"([^" + EMPH + r"]{1,200}[.:])" + EMPH + r"\s+")
MERGED_HEADING_RE = re.compile(
    r"^" + EMPH + r"([^" + EMPH + r"]{1,200}?[.:])\s+(i|A)\.\s+([^" + EMPH + r"]{1,200}[.:])" + EMPH)


def comment_markers(raw: str) -> tuple[list[tuple[int, str, str | None]], str]:
    """All comment markers that open a paragraph, and the body after the last one.

    eCFR packs levels the same way it does in regulation text:
    '1. ‡Effective date.‡ i. ‡General.‡ The Bureau's revisions ...' is comment -1
    (heading 'Effective date.') with comment -1.i inside it. A split happens only when
    an italic heading is followed by the first marker of the next level down, so
    ordinary sentences such as 'See comment A. above' are never cut.
    """
    first = _comment_marker(raw)
    if first is None:
        return [], strip_sentinels(raw)
    level, tok, rest = first
    found: list[tuple[int, str, str | None]] = []
    while True:
        # One italic run can span both headings: '‡Reasonable ... determination. i. General.‡'
        rest = MERGED_HEADING_RE.sub(EMPH + r"\1" + EMPH + r" \2. " + EMPH + r"\3" + EMPH, rest, count=1)
        h = LEAD_HEADING_RE.match(rest)
        nxt = _comment_marker(rest[h.end():]) if h else None
        if h and nxt and nxt[0] == level + 1 and nxt[1] == FIRST_OF_LEVEL.get(nxt[0]):
            found.append((level, tok, strip_sentinels(h.group(1)).strip()))
            level, tok, rest = nxt
            continue
        found.append((level, tok, None))
        return found, strip_sentinels(rest)


def parse_commentary(unit: etree._Element, identifier: str) -> list[Block]:
    blocks: list[Block] = []
    anchor: str | None = None  # '19(e)(1)(iii)', '19', 'I' (introduction) or 'app. K(b)(9)'
    anchor_heading: str | None = None
    appendix: str | None = None  # set while inside the commentary to an appendix
    stack: list[tuple[int, str]] = []  # open comment levels, outermost first

    def cite() -> str:
        base = f"Comment {anchor}" if anchor else identifier
        if not stack:
            return base
        toks = [t for _, t in stack]
        return f"{base}-{toks[0]}" + "".join(f".{t}" for t in toks[1:])

    def set_anchor(a: str, heading: str | None) -> None:
        nonlocal anchor, anchor_heading
        anchor, anchor_heading = a, heading or None
        stack.clear()

    for el in _walk(unit):
        if el.tag in TABLE_TAGS:
            md = table_to_markdown(el)
            if md:
                blocks.append(Block(path=[t for _, t in stack], citation=cite(), text=md, is_table=True,
                                    heading=anchor_heading))
            continue
        if el.tag in GRAPHIC_TAGS:
            continue
        raw = element_text(el)
        text = strip_sentinels(raw)
        if not text:
            continue

        is_heading = el.tag in HEADING_TAGS
        if is_heading or (el.tag == "P" and len(text) < 160 and
                          (SECTION_HEAD_RE.match(text) or PARA_HEAD_RE.match(text))
                          and _comment_marker(raw) is None):
            a = APPENDIX_HEAD_RE.match(text) if is_heading else None
            s = SECTION_HEAD_RE.search(text)
            p = PARA_HEAD_RE.match(text)
            ap = APPENDIX_PARA_RE.match(text) if (is_heading and appendix) else None
            if text.strip().lower() == "introduction":
                appendix = None
                set_anchor("I", None)  # cited as comment I-1 .. I-4
            elif a:
                appendix = "app. " + a.group(1)
                set_anchor(appendix, clean_heading(text[a.end():]))
            elif ap:
                set_anchor(appendix + ap.group(1), clean_heading(text[ap.end():]))
            elif p:
                appendix = None
                set_anchor(p.group(1) + p.group(2), clean_heading(text[p.end():]))
            elif s:
                appendix = None
                set_anchor(s.group(2), clean_heading(text[s.end():]))
            # Other headings (subpart titles, date captions) leave the anchor alone.
            continue

        markers, body = comment_markers(raw)
        if not markers:
            if blocks:
                blocks[-1].text += "\n" + text
            else:
                blocks.append(Block(path=[], citation=cite(), text=text, heading=anchor_heading))
            continue
        for i, (level, tok, packed_heading) in enumerate(markers):
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, tok))
            last = i == len(markers) - 1
            # A packed parent keeps its own heading as its text ('Effective date.').
            blocks.append(Block(path=[t for _, t in stack], citation=cite(),
                                text=body.strip() if last else (packed_heading or ""),
                                heading=anchor_heading))
    return blocks


# ---------------------------------------------------------------- other appendices

def parse_appendix(unit: etree._Element, identifier: str, max_words: int = 350) -> list[Block]:
    blocks: list[Block] = []
    heading: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if buf:
            citation = f"{identifier}, {heading}" if heading else identifier
            blocks.append(Block(path=[heading] if heading else [], citation=citation,
                                text="\n".join(buf), heading=heading))
            buf.clear()

    for el in _walk(unit):
        if el.tag in GRAPHIC_TAGS:
            buf.append("[graphic omitted]")
            continue
        if el.tag in TABLE_TAGS:
            flush()
            md = table_to_markdown(el)
            if md:
                blocks.append(Block(path=[heading] if heading else [],
                                    citation=f"{identifier}, {heading}" if heading else identifier,
                                    text=md, is_table=True, heading=heading))
            continue
        text = strip_sentinels(element_text(el))
        if not text:
            continue
        if el.tag in HEADING_TAGS:
            flush()
            heading = text
            continue
        buf.append(text)
        if sum(len(t.split()) for t in buf) >= max_words:
            flush()
    flush()
    return blocks


# ---------------------------------------------------------------- entry point

def parse_unit(xml: str, identifier: str, unit_type: str) -> tuple[str, DocKind, list[Block]]:
    root = etree.fromstring(xml.encode("utf-8"), parser=etree.XMLParser(recover=True, huge_tree=True))
    unit = find_unit(root, identifier)
    kind = classify(identifier, unit_type)
    heading = unit_heading(unit)
    if kind is DocKind.INTERPRETATION:
        blocks = parse_commentary(unit, identifier)
    elif kind is DocKind.APPENDIX:
        blocks = parse_appendix(unit, identifier)
    else:
        blocks = parse_section(unit, identifier)
    return heading, kind, blocks
