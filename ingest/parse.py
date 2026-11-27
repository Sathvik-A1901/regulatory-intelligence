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
COMMENT_RE = [
    re.compile(r"^(\d{1,3})\.\s+"),  # 1.
    re.compile(r"^([ivx]{1,6})\.\s+"),  # i.
    re.compile(r"^([A-Z])\.\s+"),  # A.
    re.compile(r"^" + ITALIC + r"?(\d{1,2})\.\s+"),  # italic 1.
]


def parse_commentary(unit: etree._Element, identifier: str) -> list[Block]:
    blocks: list[Block] = []
    anchor: str | None = None  # '19(e)(1)(iii)' or '19'
    anchor_heading: str | None = None
    comment: list[str] = []

    def cite() -> str:
        base = f"Comment {anchor}" if anchor else identifier
        if not comment:
            return base
        return f"{base}-{comment[0]}" + "".join(f".{c}" for c in comment[1:])

    for el in _walk(unit):
        if el.tag in TABLE_TAGS:
            md = table_to_markdown(el)
            if md:
                blocks.append(Block(path=list(comment), citation=cite(), text=md, is_table=True,
                                    heading=anchor_heading))
            continue
        if el.tag in GRAPHIC_TAGS:
            continue
        text = strip_sentinels(element_text(el))
        if not text:
            continue

        if el.tag in HEADING_TAGS or (el.tag == "P" and len(text) < 160 and
                                      (SECTION_HEAD_RE.match(text) or PARA_HEAD_RE.match(text))
                                      and not COMMENT_RE[0].match(text)):
            s = SECTION_HEAD_RE.search(text)
            p = PARA_HEAD_RE.match(text)
            if p:
                anchor = p.group(1) + p.group(2)
                anchor_heading = clean_heading(text[p.end():]) or None
                comment = []
            elif s:
                anchor = s.group(2) if "." in s.group(0) else s.group(1)
                anchor_heading = clean_heading(text[s.end():]) or None
                comment = []
            continue

        level, tok, body = None, None, text
        for i, rx in enumerate(COMMENT_RE):
            m = rx.match(text)
            if m:
                # 'i.' after 'h.' never happens here: commentary has no lowercase-letter level.
                level, tok, body = i, m.group(1), text[m.end():]
                break
        if level is None:
            if blocks:
                blocks[-1].text += "\n" + text
            else:
                blocks.append(Block(path=[], citation=cite(), text=text, heading=anchor_heading))
            continue
        comment = comment[:level] + [tok]
        blocks.append(Block(path=list(comment), citation=cite(), text=body.strip(),
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
