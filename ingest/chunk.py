"""Clause-level chunking with version-interval compaction.

Input: every dated version of one unit (section, appendix, commentary).
Output: chunks whose [valid_from, valid_to) interval says exactly when that text
was in force, with `superseded_by` pointing at the replacing text.

Compaction: a paragraph that reads the same across ten amendments of its section
is one chunk with one long interval, not ten duplicates. That keeps the index
small and, more importantly, stops identical text from crowding out the top-k.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ingest.metadata import AccessClass, Block, Chunk, SectionVersion, make_chunk_id

MAX_WORDS = 300  # long paragraphs are split on sentence boundaries above this


DASH_VARIANTS = re.compile(r"\s*(?:—|–|\s-\s|--)\s*")
BLANK_LINE = re.compile(r"_{2,}")


def _norm(text: str) -> str:
    """Comparison key for 'did this paragraph's text change between versions?'.

    eCFR re-typesets old text without amending it: on 2023-03-20 every ' - ' in
    Supplement I became '—' on a non-substantive date. Treating that as a change would
    close ~290 commentary chunks as superseded when nothing was amended. Only the
    comparison is normalized; the stored text is left exactly as published.
    """
    text = DASH_VARIANTS.sub("—", text)
    text = BLANK_LINE.sub("__", text)  # fill-in blanks on model forms vary in length
    return re.sub(r"\s+", " ", text).strip().lower()


def split_long(text: str, max_words: int = MAX_WORDS) -> list[str]:
    if len(text.split()) <= max_words:
        return [text]
    sentences = re.split(r"(?<=[.;:])\s+(?=[A-Z(])", text)
    parts, buf = [], []
    for s in sentences:
        if buf and len(" ".join(buf + [s]).split()) > max_words:
            parts.append(" ".join(buf))
            buf = []
        buf.append(s)
    if buf:
        parts.append(" ".join(buf))
    return parts


def context_for(block: Block, version: SectionVersion) -> str:
    """'12 CFR § 1026.43 Minimum standards ... > 1026.43(e) Qualified mortgages > 1026.43(e)(2) ...'.

    Prepended to the chunk at index time so a paragraph that reads "(vi) For which the
    ratio ... does not exceed 43 percent" is still findable by "qualified mortgage".
    """
    crumbs = [f"{version.title} CFR {version.heading}"]
    crumbs += [f"{cite} {heading}" for cite, heading in block.ancestors]
    if block.heading:
        crumbs.append(f"{block.citation} {block.heading}")
    return " > ".join(dict.fromkeys(crumbs))


@dataclass
class _Open:
    chunk: Chunk
    norm: str
    seen_in: list[str] = field(default_factory=list)


def chunk_versions(
    versions: list[SectionVersion],
    access_class: AccessClass = AccessClass.PUBLIC,
    regulator: str = "CFPB",
    jurisdiction: str = "US-federal",
) -> list[Chunk]:
    """Turn the version history of one unit into interval-tagged chunks."""
    versions = sorted(versions, key=lambda v: v.valid_from)
    open_by_cite: dict[str, _Open] = {}
    closed: list[Chunk] = []

    for v in versions:
        if v.removed:
            for o in open_by_cite.values():
                o.chunk.valid_to = v.valid_from
                closed.append(o.chunk)
            open_by_cite = {}
            continue

        current: dict[str, tuple[str, str, Block]] = {}  # citation -> (text, context, block)
        for b in v.blocks:
            pieces = [b.text] if b.is_table else split_long(b.text)
            for i, piece in enumerate(pieces):
                cite = b.citation if len(pieces) == 1 else f"{b.citation} [part {i + 1}]"
                if cite in current:  # same citation twice in one version: concatenate
                    prev_text, ctx, pb = current[cite]
                    current[cite] = (prev_text + "\n" + piece, ctx, pb)
                else:
                    current[cite] = (piece, context_for(b, v), b)

        # Close chunks whose citation vanished or whose text changed.
        for cite in list(open_by_cite):
            o = open_by_cite[cite]
            if cite not in current or _norm(current[cite][0]) != o.norm:
                o.chunk.valid_to = v.valid_from
                closed.append(o.chunk)
                del open_by_cite[cite]
                if cite in current:
                    o.chunk.superseded_by = make_chunk_id(v.title, cite, v.valid_from)

        # Open chunks for new or changed text; extend unchanged ones.
        for cite, (text, ctx, b) in current.items():
            if cite in open_by_cite:
                open_by_cite[cite].seen_in.append(v.version_id)
                continue
            chunk = Chunk(
                chunk_id=make_chunk_id(v.title, cite, v.valid_from),
                title=v.title,
                part=v.part,
                section=v.identifier,
                section_heading=v.heading,
                citation=cite,
                kind=v.kind,
                text=text,
                context=ctx,
                is_table=b.is_table,
                jurisdiction=jurisdiction,
                regulator=regulator,
                access_class=access_class,
                valid_from=v.valid_from,
                valid_to=None,
                document_version=v.version_id,
                amending_documents=list(v.amending_documents),
                parent_id=f"{v.title}cfr{v.identifier}",  # resolved to a version at query time
            )
            open_by_cite[cite] = _Open(chunk=chunk, norm=_norm(text), seen_in=[v.version_id])

    # Anything still open is in force today, unless the unit's last version has an end date.
    last_to = versions[-1].valid_to if versions else None
    for o in open_by_cite.values():
        o.chunk.valid_to = last_to
        closed.append(o.chunk)
    return closed


def assign_intervals(dates: list[date]) -> list[tuple[date, date | None]]:
    """[d1, d2, d3] -> [(d1, d2), (d2, d3), (d3, None)]."""
    ds = sorted(set(dates))
    return [(d, ds[i + 1] if i + 1 < len(ds) else None) for i, d in enumerate(ds)]
