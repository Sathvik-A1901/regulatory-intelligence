"""Core data model.

Every chunk carries the metadata the query path filters on *before* ranking:
the interval during which its text was in force, what superseded it, the
jurisdiction, and the access class that the entitlement layer maps to roles.
"""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class DocKind(StrEnum):
    REGULATION = "regulation"  # numbered section text, e.g. 12 CFR 1026.19
    APPENDIX = "appendix"  # model forms, appendices
    INTERPRETATION = "interpretation"  # official staff commentary (Supplement I)
    SOP = "sop"  # internal procedures written for this project


class AccessClass(StrEnum):
    """Document classes, not roles. config/entitlements.yaml maps roles to classes."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    SUPERVISORY = "supervisory"


class Block(BaseModel):
    """One structural unit from the parser: a paragraph, a table, or a comment."""

    path: list[str] = Field(description="Paragraph markers from the section down, e.g. ['e','1','iii']")
    citation: str = Field(description="Human citation, e.g. '1026.19(e)(1)(iii)' or 'Comment 19(e)(1)(iii)-1'")
    text: str
    is_table: bool = False
    heading: str | None = None
    ancestors: list[tuple[str, str]] = Field(
        default_factory=list,
        description="(citation, heading) for each enclosing paragraph that has a heading",
    )


class SectionVersion(BaseModel):
    """A section, appendix or commentary unit as it read on one effective date."""

    title: int
    part: str
    identifier: str  # eCFR identifier, e.g. '1026.19' or 'Supplement I to Part 1026'
    heading: str
    kind: DocKind
    valid_from: date
    valid_to: date | None = None  # exclusive; None means still in force
    removed: bool = False
    substantive: bool = True
    amending_documents: list[str] = Field(default_factory=list)  # Federal Register citations
    blocks: list[Block] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def version_id(self) -> str:
        return f"{self.title}cfr{self.identifier}@{self.valid_from.isoformat()}"


class Chunk(BaseModel):
    """The unit that gets indexed. Small enough to match, linked to its parent section."""

    chunk_id: str
    title: int
    part: str
    section: str  # eCFR identifier of the parent unit
    section_heading: str
    citation: str
    kind: DocKind
    text: str
    context: str = Field("", description="Breadcrumb of ancestor headings, prepended at index time")
    is_table: bool = False
    jurisdiction: str = "US-federal"
    regulator: str = "CFPB"
    access_class: AccessClass = AccessClass.PUBLIC
    valid_from: date
    valid_to: date | None = None
    superseded_by: str | None = None  # chunk_id of the text that replaced this one
    document_version: str  # version_id of the section version this chunk first appeared in
    amending_documents: list[str] = Field(default_factory=list)
    parent_id: str  # unit id; the version in force on the as-of date is the parent passage

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    def in_force_on(self, as_of: date) -> bool:
        return self.valid_from <= as_of and (self.valid_to is None or as_of < self.valid_to)


def make_chunk_id(title: int, citation: str, valid_from: date) -> str:
    raw = f"{title}|{citation}|{valid_from.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
