"""Run the evaluation suite.

    python -m evals.run_suite                 # score data/processed/chunks.jsonl against the golden set
    python -m evals.run_suite --smoke         # score a tiny corpus built from tests/fixtures (what CI runs)
    python -m evals.run_suite --min-recall 0.6  # exit 1 below threshold: the merge gate

Writes evals/results/<retriever>_latest.json and, inside GitHub Actions, appends the
scorecard to the job summary so every pull request shows its numbers.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from evals.harness import evaluate, load_chunks, load_golden, scorecard_markdown, write_report
from retrieval.bm25 import BM25Retriever

ROOT = Path(__file__).resolve().parent.parent


def smoke_corpus():
    """Two versions of a fixture section plus one current section: enough to exercise as-of filtering."""
    from ingest.chunk import chunk_versions
    from ingest.metadata import SectionVersion
    from ingest.parse import parse_unit

    fx = ROOT / "tests" / "fixtures"
    chunks = []
    plan = {
        "1026.43": [("section_1026_43_v2019.xml", date(2017, 1, 3), date(2021, 3, 1)),
                    ("section_1026_43_v2021.xml", date(2021, 3, 1), None)],
        "1026.19": [("section_1026_19.xml", date(2017, 1, 3), None)],
    }
    for ident, files in plan.items():
        versions = []
        for name, start, end in files:
            heading, kind, blocks = parse_unit((fx / name).read_text(encoding="utf-8"), ident, "section")
            versions.append(SectionVersion(title=12, part="1026", identifier=ident, heading=heading,
                                           kind=kind, valid_from=start, valid_to=end, blocks=blocks))
        chunks += chunk_versions(versions)
    return chunks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, default=ROOT / "data" / "processed" / "chunks.jsonl")
    ap.add_argument("--golden", type=Path, default=None)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--verified-only", action="store_true")
    ap.add_argument("--min-recall", type=float, default=0.0)
    ap.add_argument("--smoke", action="store_true", help="Use the fixture corpus and smoke golden set")
    args = ap.parse_args(argv)

    if args.smoke:
        chunks = smoke_corpus()
        golden = args.golden or ROOT / "evals" / "golden" / "smoke.yaml"
    else:
        if not args.chunks.exists():
            print(f"No corpus at {args.chunks}. Run: python -m ingest.flow", file=sys.stderr)
            return 2
        chunks = load_chunks(args.chunks)
        golden = args.golden or ROOT / "evals" / "golden" / "golden.yaml"

    retriever = BM25Retriever(chunks)
    report = evaluate(retriever, load_golden(golden), k=args.k, include_drafts=not args.verified_only)
    path = write_report(report, ROOT / "evals" / "results")

    card = scorecard_markdown(report)
    print(card)
    for item in report["items"]:
        if item["missing"]:
            print(f"  miss {item['id']}: expected {item['missing']}, got {item['top'][:3]}")
    print(f"\n{len(chunks)} chunks; wrote {path.relative_to(ROOT)}")

    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Retrieval eval ({'smoke' if args.smoke else 'full'})\n\n{card}\n")

    if report[f"recall@{args.k}"] < args.min_recall:
        print(f"FAIL: recall@{args.k} {report[f'recall@{args.k}']:.3f} < {args.min_recall}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
