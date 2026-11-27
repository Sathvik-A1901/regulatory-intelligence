"""CFR paragraph-marker hierarchy.

Federal regulations number paragraphs in a fixed six-level scheme:

    (a)  lowercase letter
    (1)  arabic numeral
    (i)  lowercase roman
    (A)  uppercase letter
    (1)  italic arabic
    (i)  italic roman

The hard part is that some tokens are ambiguous: '(i)' is both the ninth letter and
the first roman numeral, '(v)' and '(x)' likewise. A fixed-size splitter never sees
this; a citation-producing parser has to resolve it from what came before.
"""

from __future__ import annotations

import re

ITALIC = "†"  # sentinel the XML layer inserts for italic markers: (<I>1</I>) -> (†1)

ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18,
    "xix": 19, "xx": 20, "xxi": 21, "xxii": 22, "xxiii": 23, "xxiv": 24, "xxv": 25,
}

LOWER, DIGIT, ROMAN_L, UPPER, IT_DIGIT, IT_ROMAN = 1, 2, 3, 4, 5, 6


def _letter_index(tok: str) -> int | None:
    """a=1 .. z=26, aa=27 .. zz=52 (the CFR doubles letters past z)."""
    if re.fullmatch(r"[a-z]", tok):
        return ord(tok) - 96
    if re.fullmatch(r"([a-z])\1", tok):
        return 26 + ord(tok[0]) - 96
    return None


def _upper_index(tok: str) -> int | None:
    if re.fullmatch(r"[A-Z]", tok):
        return ord(tok) - 64
    if re.fullmatch(r"([A-Z])\1", tok):
        return 26 + ord(tok[0]) - 64
    return None


def ordinal(level: int, tok: str) -> int | None:
    if level == LOWER:
        return _letter_index(tok)
    if level in (DIGIT, IT_DIGIT):
        return int(tok) if tok.isdigit() else None
    if level in (ROMAN_L, IT_ROMAN):
        return ROMAN.get(tok)
    if level == UPPER:
        return _upper_index(tok)
    return None


def candidate_levels(tok: str, italic: bool) -> list[int]:
    if italic:
        if tok.isdigit():
            return [IT_DIGIT]
        return [IT_ROMAN] if tok in ROMAN else []
    out = []
    if _letter_index(tok) is not None:
        out.append(LOWER)
    if tok.isdigit():
        out.append(DIGIT)
    if tok in ROMAN:
        out.append(ROMAN_L)
    if _upper_index(tok) is not None:
        out.append(UPPER)
    return out


class MarkerStack:
    """Tracks the current position in the hierarchy and resolves each new marker."""

    def __init__(self) -> None:
        self.stack: list[tuple[int, str]] = []  # (level, token), outermost first

    def path(self) -> list[str]:
        return [tok for _, tok in self.stack]

    def push(self, tok: str, italic: bool = False) -> bool:
        """Place a marker. Returns False if it fits nowhere (caller treats it as text)."""
        cands = candidate_levels(tok, italic)
        if not cands:
            return False
        depth_levels = [lvl for lvl, _ in self.stack]

        chosen: int | None = None
        # 1. The token continues a sequence already open in the stack.
        for lvl in cands:
            if lvl in depth_levels:
                prev = self.stack[depth_levels.index(lvl)][1]
                po, to = ordinal(lvl, prev), ordinal(lvl, tok)
                if po is not None and to == po + 1:
                    chosen = lvl
                    break
        # 2. The token opens the next level down.
        if chosen is None:
            next_level = (self.stack[-1][0] + 1) if self.stack else LOWER
            for lvl in cands:
                if lvl == next_level and ordinal(lvl, tok) == 1:
                    chosen = lvl
                    break
        # 3. A first-of-sequence token at any deeper level (skipped levels happen).
        if chosen is None:
            cur = self.stack[-1][0] if self.stack else 0
            for lvl in cands:
                if lvl > cur and ordinal(lvl, tok) == 1:
                    chosen = lvl
                    break
        # 4. Fall back to the shallowest candidate that is not deeper than one past current.
        if chosen is None:
            cur = self.stack[-1][0] if self.stack else 0
            fits = [lvl for lvl in cands if lvl <= cur + 1]
            if not fits:
                return False
            chosen = fits[0]

        while self.stack and self.stack[-1][0] >= chosen:
            self.stack.pop()
        self.stack.append((chosen, tok))
        return True


EMPH = "‡"  # sentinel the XML layer wraps around italic runs: <I>Timing.</I> -> ‡Timing.‡

MARKER_RE = re.compile(r"\s*\((" + ITALIC + r"?)([a-zA-Z]{1,5}|\d{1,3})\)")
HEADING_RE = re.compile(r"\s*" + EMPH + r"([^" + EMPH + r"]{1,200})" + EMPH + r"\s*—?")


def clean_heading(h: str) -> str:
    return h.strip().strip("—.:- ").strip()


def strip_sentinels(text: str) -> str:
    return text.replace(ITALIC, "").replace(EMPH, "")


def split_leading_markers(text: str) -> list[tuple[str, bool, str | None, int, int]]:
    """Find the run of paragraph markers that opens a paragraph.

    Handles the eCFR habit of packing several levels into one paragraph:
    '(e) ‡Mortgage loans‡—(1) ‡Provision of disclosures‡—(i) ‡Creditor.‡ body'.

    Returns [(token, italic, heading_or_None, start, end), ...] where start/end are
    offsets in `text`, so a caller that rejects a marker can recover the text.
    """
    found: list[tuple[str, bool, str | None, int, int]] = []
    pos = 0
    while True:
        m = MARKER_RE.match(text, pos)
        if not m:
            break
        italic, tok = bool(m.group(1)), m.group(2)
        start, pos = m.start(), m.end()
        heading = None
        h = HEADING_RE.match(text, pos)
        if h:
            heading = clean_heading(h.group(1))
            pos = h.end()
        found.append((tok, italic, heading or None, start, pos))
    return found
