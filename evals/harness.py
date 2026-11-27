"""Retrieval-stage evaluation harness.

Phase 1 scores retrieval only: does the top-k contain the clause a correct answer
must cite, in the version that was in force on the question's date? Generation
metrics (RAGAS faithfulness, citation accuracy, abstention correctness) plug into
the same golden set in later phases.

Metrics
  recall@k        share of expected citations matched by a chunk in the top k
  mrr             reciprocal rank of the first matching chunk
  stale_rate@k    share of top-k chunks NOT in force on the as-of date.
                  Reported for the filtered retriever (should be 0) and for the same
                  retriever with the as-of filter switched off, so the README can show
                  what version-aware retrieval actually buys.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from statistics import mean

import yaml
from pydantic import BaseModel

from ingest.metadata import Chunk
from retrieval.entitlements import Policy, ablation_filter_ignoring_dates, build_filter


class GoldenItem(BaseModel):
    id: str
    question: str
    as_of: date
    role: str = "public"
    expected_citations: list[str] = []
    answer_points: list[str] = []
    should_abstain: bool = False
    status: str = "draft"
    notes: str | None = None


def load_golden(path: Path) -> list[GoldenItem]:
    return [GoldenItem(**row) for row in yaml.safe_load(path.read_text(encoding="utf-8"))]


def load_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as fh:
        return [Chunk.model_validate_json(line) for line in fh if line.strip()]


def citation_matches(expected: str, got: str) -> bool:
    """Same clause, or a descendant of it. '1026.19(e)(1)' matches '1026.19(e)(1)(iii)'."""
    got = got.split(" [part")[0]
    return got == expected or got.startswith(expected + "(") or got.startswith(expected + "-")


@dataclass
class ItemResult:
    id: str
    status: str
    recall: float
    rr: float
    stale_rate: float
    stale_rate_unfiltered: float
    top: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def evaluate(retriever, items: list[GoldenItem], k: int = 10, policy: Policy | None = None,
             include_drafts: bool = True) -> dict:
    results: list[ItemResult] = []
    abstain_items = []
    for it in items:
        if it.status != "verified" and not include_drafts:
            continue
        if it.should_abstain:
            abstain_items.append(it.id)  # scored at the generation stage, not here
            continue
        hits = retriever.search(it.question, build_filter(it.role, it.as_of, policy), k=k)
        cites = [h.chunk.citation for h in hits]

        found = [e for e in it.expected_citations if any(citation_matches(e, c) for c in cites)]
        first = next((i for i, c in enumerate(cites)
                      if any(citation_matches(e, c) for e in it.expected_citations)), None)

        # Ablation: same retriever with the version filter disabled.
        raw = retriever.search(it.question, ablation_filter_ignoring_dates(it.role, policy), k=k)
        results.append(ItemResult(
            id=it.id,
            status=it.status,
            recall=len(found) / len(it.expected_citations) if it.expected_citations else 1.0,
            rr=1.0 / (first + 1) if first is not None else 0.0,
            stale_rate=_stale(hits, it.as_of),
            stale_rate_unfiltered=_stale(raw, it.as_of),
            top=cites[:5],
            missing=[e for e in it.expected_citations if e not in found],
        ))

    def agg(attr: str) -> float:
        return round(mean(getattr(r, attr) for r in results), 4) if results else 0.0

    return {
        "retriever": getattr(retriever, "name", type(retriever).__name__),
        "k": k,
        "n_scored": len(results),
        "n_verified": sum(r.status == "verified" for r in results),
        "n_abstain_deferred": len(abstain_items),
        f"recall@{k}": agg("recall"),
        "mrr": agg("rr"),
        f"stale_rate@{k}": agg("stale_rate"),
        f"stale_rate@{k}_without_asof_filter": agg("stale_rate_unfiltered"),
        "items": [asdict(r) for r in results],
    }


def _stale(hits, as_of: date) -> float:
    if not hits:
        return 0.0
    return sum(not h.chunk.in_force_on(as_of) for h in hits) / len(hits)


def scorecard_markdown(report: dict) -> str:
    k = report["k"]
    lines = [
        f"| Metric | {report['retriever']} |",
        "|---|---|",
        f"| recall@{k} | {report[f'recall@{k}']:.3f} |",
        f"| MRR | {report['mrr']:.3f} |",
        f"| stale results in top {k}, as-of filter on | {report[f'stale_rate@{k}']:.1%} |",
        f"| stale results in top {k}, as-of filter off | {report[f'stale_rate@{k}_without_asof_filter']:.1%} |",
        f"| items scored (verified) | {report['n_scored']} ({report['n_verified']}) |",
    ]
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report['retriever']}_latest.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
