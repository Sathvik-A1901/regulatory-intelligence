# Regulatory Intelligence

**A governed retrieval service for financial regulation: version-aware, permission-aware, and gated by an evaluation suite that runs on every pull request.**

Between 40% and 60% of enterprise RAG projects never reach production, and the reasons are rarely the model: stale chunks that outrank the rule now in force, permissions applied after retrieval, chunking that destroys clause numbering and tables, no measured abstention, and no regression gate. This project is built around those five failures. The question it is designed to answer well:

> *"What were the requirements for X as of March 2024, and exactly which clause says so?"*

**Corpus:** three CFPB regulations, with every version in force since January 2017: Regulation Z (12 CFR 1026, Truth in Lending), Regulation B (12 CFR 1002, Equal Credit Opportunity) and Regulation E (12 CFR 1005, Electronic Fund Transfers), including the official staff interpretations.

## Status

| Week | Deliverable | State |
|---|---|---|
| 1 | Corpus ingestion, eval skeleton in CI | **Built.** Ingestion is tested on fixtures; first live run pending |
| 2 | Structure-aware parsing and chunking, parse-quality report | Parser built; report published after the first live run |
| 3 | Hybrid retrieval and reranking, ablation table | BM25 baseline only |
| 4 | Version-aware retrieval | As-of filtering built and tested on fixtures |
| 5 | ACL enforcement and red-team suite | Entitlement gate built; red-team suite not started |
| 6 | Corrective loop, abstention, MCP server | Not started |

## How it works so far

**1. Every version, not just today's.** `ingest/sources/ecfr.py` asks the eCFR versioner API for every date each section changed since 2017-01-03, then fetches each section as it read on each of those dates. Past versions never change, so every response is cached to disk permanently, and a re-run only fetches dates it has not seen. `ingest/sources/federal_register.py` matches each change date to the final rule that took effect that day, so every chunk can name the rule document that put it in force.

**2. Parse the legal structure.** `ingest/markers.py` and `ingest/parse.py` resolve the six-level CFR paragraph hierarchy, (a) (1) (i) (A) *(1)* *(i)*, including the ambiguous cases: `(i)` after `(h)` is a letter, `(i)` under `(1)` is a roman numeral. They also unpack eCFR's habit of packing several levels into one paragraph (`(e) Mortgage loans—(1) Provision of disclosures—(i) Creditor.`). Every block gets an exact citation such as `1026.19(e)(1)(iii)`. Tables stay whole as Markdown. Supplement I commentary gets its own parser with citations such as `Comment 19(e)(1)(iii)-1.i`.

**3. Give every chunk an in-force window.** `ingest/chunk.py` walks each section's versions in date order. A paragraph whose text never changed is one chunk with one long window, not one duplicate per amendment. A paragraph whose text changed is closed on the amendment date and points to its replacement through `superseded_by`. Each chunk also carries a breadcrumb of its ancestor headings, so `(vi) ... does not exceed 43 percent` is still findable by "qualified mortgage".

**4. Filter before ranking, in one place.** `retrieval/entitlements.py` is the policy decision point. It turns *who is asking* and *as of when* into a sealed `RetrievalFilter` and writes an audit record for every decision. Retrievers accept nothing else, so a chunk the asker may not see, or one that was not in force on the date, never competes for a rank. Tests enforce the rule: a filter cannot be built outside that module, a retriever rejects a bare date, and a static check fails the build if any other retrieval module reads access or date fields itself.

**5. Measure from the first commit.** `evals/run_suite.py` scores recall@k and MRR against a golden set. It also re-runs every question with the date filter switched off and reports how many superseded chunks reach the top k. That is the number that shows what version-aware retrieval buys.

## Evaluation

| Metric | Target | Measured |
|---|---|---|
| Recall@10, BM25 baseline | — | pending first live ingestion |
| Stale results in top 10, as-of filter on | 0% | 0% on the smoke corpus |
| Stale results in top 10, as-of filter off | — | reported for comparison |
| Faithfulness (RAGAS) | ≥ 0.90 | Phase 6 |
| Citation accuracy | ≥ 0.95 | Phase 6 |
| Abstention correctness | ≥ 0.90 | Phase 6 |
| ACL leaks across red-team suite | **0** | Phase 5 |

`evals/golden/golden.yaml` holds 16 seed questions, including two as-of pairs that straddle a rule change and four that should be refused. **Every one is a draft** until its citation has been checked by hand in the eCFR on its as-of date. Drafts are never reported as results.

CI (`.github/workflows/evals.yml`) runs lint, the unit tests and the eval suite on a fixture corpus on every pull request. It fails on any regression there.

## Running it

Python 3.12+. On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
$env:REGINTEL_CONTACT = "your.email@example.edu"

pytest -q                                          # parser, versioning, entitlement gate
python -m evals.run_suite --smoke                  # eval harness on the fixture corpus

python -m ingest.flow --only 1026.19 --only 1026.43   # small live run first
python -m ingest.flow                                 # all three regulations
python -m evals.run_suite                             # score the real corpus
```

Ingestion writes `data/processed/chunks.jsonl`, the parsed versions of every unit (`units/`), a `manifest.json` that records what changed since the last run, and a `parse_report.json` listing units to inspect by hand.

## What is real and what is synthetic

| Component | Status |
|---|---|
| Regulatory text and version history | **Real**: eCFR versioner API |
| Effective dates and supersession | **Real**: eCFR version dates, linked to Federal Register final rules |
| Internal SOPs | **Authored** for this project (Phase 5) |
| Entitlements | **Synthetic** |
| Test fixtures in `tests/fixtures/` | **Abridged** eCFR-shaped XML, not authoritative text |

No public corpus ships with access-control lists, so the entitlement model in `config/entitlements.yaml` is invented: four roles (public, analyst, compliance reviewer, supervisor), with entitlements attached to document classes rather than individual chunks. The *enforcement* is real engineering; the *assignment* is made up.

## Repository layout

```
ingest/
  sources/        ecfr.py, federal_register.py, http.py (polite client, permanent cache)
  markers.py      CFR paragraph-hierarchy resolution
  parse.py        eCFR XML -> cited blocks; tables; Supplement I commentary
  chunk.py        clause chunks with in-force windows and supersession
  metadata.py     Chunk and SectionVersion models
  flow.py         the ingestion pipeline (Prefect in Phase 7)
retrieval/
  entitlements.py policy decision point: the only place a filter is built
  bm25.py         baseline retriever
api/, mcp_server/ Phase 6
evals/
  golden/         golden.yaml (drafts), smoke.yaml (CI)
  redteam/        Phase 5
  harness.py, run_suite.py
config/           corpus.yaml, entitlements.yaml
tests/
```

## Licence

MIT. Regulatory text is a US government work and in the public domain.
