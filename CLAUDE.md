# Regulatory Intelligence — project context

Repo: https://github.com/Sathvik-A1901/regulatory-intelligence (main, CI green)
Goal: portfolio RAG project for AI-engineer roles. A governed retrieval service over
CFPB regulations that is version-aware (answers "as of" a date), permission-aware
(ACL filter inside the query), and gated by evals in CI on every PR.

Corpus: 12 CFR 1026 (Reg Z), 1002 (Reg B), 1005 (Reg E), every eCFR version since
2017-01-03, including Supplement I official interpretations. Config: config/corpus.yaml.
LLM for later phases: Anthropic API (not used yet).

## Built (week 1)
- ingest/sources/: eCFR versioner + Federal Register clients, polite HTTP, permanent disk cache
- ingest/markers.py + parse.py: CFR paragraph hierarchy (a)(1)(i)(A)(1)(i), resolves
  ambiguous (i)/(v)/(x), packed markers "(e) X—(1) Y—(i) Z.", tables as Markdown,
  Supplement I citations like "Comment 19(e)(1)(iii)-1.i"
- ingest/chunk.py: clause chunks with valid_from/valid_to, superseded_by, unchanged
  paragraphs compacted across versions, ancestor-heading breadcrumb in `context`
- ingest/flow.py: `python -m ingest.flow [--only 1026.19] [--no-fr]` -> data/processed/
  (chunks.jsonl, units/, manifest.json, parse_report.json)
- retrieval/entitlements.py: the ONLY place a RetrievalFilter (role + as_of) is built;
  audit-logs every decision. Design rule enforced by tests/test_entitlements.py
  (sealed filter, retrievers reject bare dates, static check on retrieval/).
- retrieval/bm25.py: baseline retriever, filters before ranking
- evals/: harness.py (recall@k, MRR, stale-rate with/without as-of filter),
  run_suite.py (`--smoke` for CI, `--min-recall` gate), golden/golden.yaml (16 DRAFT
  items, none verified), golden/smoke.yaml (CI)
- .github/workflows/evals.yml: ruff, pytest (28 tests), smoke eval gate

## First live run (2026-11-26): 1026.19 + 1026.43
- `python -m ingest.flow --only 1026.19 --only 1026.43`: 15 versions, 18 fetches, 0 failures,
  441 chunks (401 in force today). Every marked <P> in every version maps to exactly one
  block, no duplicate citations.
- Two parser bugs found on real XML and fixed (regression tests in tests/test_parse.py):
  1. Defined terms "(1) <I>Covered transaction</I> means ..." were stripped as headings;
     body now keeps the term (heading still recorded for the breadcrumb).
  2. Pre-2022 versions write the heading dash as ASCII " - " not "—", which broke packed
     markers ((c)(5)(A) instead of (c)(5)(i)(A)) and created ~190 spurious supersessions
     on non-substantive version dates. Chunk count fell 631 -> 441 after the fix.
- Not yet run: Supplement I and appendices on live XML, Regs B and E.
- Observed: very short list-item chunks ("Prepaid interest;") exist; consider merging
  into parent at index time if they hurt retrieval.
- Golden set citations must still be hand-checked in eCFR before any number is published.

## Next steps
1. Live run on Supplement I to Part 1026 and one appendix (commentary parser is the
   least tested path), then all of Reg Z.
2. Week 2: full ingestion + published parse-quality report.
3. Week 3: dense (pgvector) + BM25 (OpenSearch) hybrid with RRF, cross-encoder rerank,
   ablation table. Keep every retriever taking a RetrievalFilter.
4. Week 5: authored SOPs (internal/restricted classes) + ACL red-team suite, zero leaks.
5. Week 6: corrective loop, abstention, grounded answers (Anthropic), FastAPI + MCP server.
Known limit: BM25 IDF is computed over all chunks incl. restricted (term-rarity side channel).

## Conventions
- Python 3.12+, `pip install -e ".[dev]"`, set REGINTEL_CONTACT env var (User-Agent email).
- Before committing: `ruff check .`, `pytest -q`, `python -m evals.run_suite --smoke --min-recall 1.0`.
- README states plainly what is real vs synthetic (entitlements are synthetic; fixtures abridged).
- Never report draft golden items as results.
