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
- .github/workflows/evals.yml: ruff, pytest (25 tests), smoke eval gate

## Not yet verified
- Never run against live eCFR. Parser tested only on abridged fixtures in tests/fixtures/.
- Golden set citations must be hand-checked in eCFR before any number is published.

## Next steps
1. First live run: `python -m ingest.flow --only 1026.19 --only 1026.43`; fix parser
   issues found on real XML; inspect parse_report.json.
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
