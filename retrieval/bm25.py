"""Baseline retriever: BM25 over the chunks a RetrievalFilter admits.

This is the floor every later component (dense, hybrid, rerank) has to beat, and the
first row of the ablation table. It exists in week one so the eval harness produces a
real number from the first commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from ingest.metadata import Chunk
from retrieval.entitlements import RetrievalFilter

TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = set("the a an of to and or in for on by with is are be as that this any its it at from".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class BM25Retriever:
    name = "bm25"

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(f"{c.context} {c.citation} {c.text}") for c in chunks])

    def search(self, query: str, flt: RetrievalFilter, k: int = 10) -> list[Hit]:
        if not isinstance(flt, RetrievalFilter):
            raise TypeError("search() requires a RetrievalFilter from retrieval.entitlements")
        # Filter first, then rank only what is left: an inadmissible chunk never competes.
        # Known limit of this baseline: IDF statistics are computed over the whole corpus,
        # restricted chunks included. No text leaks, but term rarity does; the indexed
        # retrievers in Phase 2 should keep restricted classes in separate indexes.
        admitted = [i for i, c in enumerate(self.chunks) if flt.admits(c)]
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(admitted, key=lambda i: scores[i], reverse=True)[:k]
        return [Hit(self.chunks[i], float(scores[i])) for i in ranked]
