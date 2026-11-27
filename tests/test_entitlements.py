"""The design rule: no retrieval query is built without going through entitlements.py."""

import ast
from datetime import date
from pathlib import Path

import pytest

from ingest.metadata import AccessClass, Chunk, DocKind
from retrieval.bm25 import BM25Retriever
from retrieval.entitlements import (
    RetrievalFilter,
    ablation_filter_ignoring_dates,
    build_filter,
)

RETRIEVAL_DIR = Path(__file__).resolve().parent.parent / "retrieval"
GATE_ONLY = {"access_class", "in_force_on", "valid_from", "valid_to", "allowed_classes"}


def _chunk(cid: str, text: str, cls: AccessClass, start=date(2017, 1, 3), end=None) -> Chunk:
    return Chunk(chunk_id=cid, title=12, part="1026", section="1026.99", section_heading="§ 1026.99 Test",
                 citation=f"1026.99({cid})", kind=DocKind.REGULATION, text=text, access_class=cls,
                 valid_from=start, valid_to=end, document_version="v", parent_id="p")


CORPUS = [
    _chunk("a", "escalation procedure for overdraft complaints", AccessClass.PUBLIC),
    _chunk("b", "escalation procedure for overdraft complaints internal", AccessClass.INTERNAL),
    _chunk("c", "escalation procedure for overdraft complaints restricted", AccessClass.RESTRICTED),
]


def test_filter_cannot_be_built_outside_entitlements():
    with pytest.raises(PermissionError):
        RetrievalFilter(role="supervisor", allowed_classes=frozenset({"restricted"}), as_of=date.today())


def test_retriever_rejects_anything_but_a_filter():
    r = BM25Retriever(CORPUS)
    with pytest.raises(TypeError):
        r.search("overdraft", date.today())  # a bare date is not an authorisation decision


def test_public_role_never_sees_restricted_text():
    r = BM25Retriever(CORPUS)
    hits = r.search("restricted internal escalation", build_filter("public", date.today()), k=10)
    assert {h.chunk.access_class for h in hits} == {AccessClass.PUBLIC}


def test_unknown_role_is_denied_everything():
    r = BM25Retriever(CORPUS)
    assert r.search("overdraft", build_filter("intern", date.today()), k=10) == []


def test_date_ablation_still_enforces_entitlements():
    r = BM25Retriever(CORPUS)
    hits = r.search("restricted", ablation_filter_ignoring_dates("analyst"), k=10)
    assert AccessClass.RESTRICTED not in {h.chunk.access_class for h in hits}


def test_audit_log_records_every_decision(caplog):
    with caplog.at_level("INFO", logger="regintel.audit"):
        build_filter("analyst", date(2024, 6, 1))
        build_filter("nobody", date(2024, 6, 1))
    decisions = [r.getMessage() for r in caplog.records]
    assert any('"decision": "allow"' in d and '"analyst"' in d for d in decisions)
    assert any('"decision": "deny"' in d and '"nobody"' in d for d in decisions)


def test_no_other_retrieval_module_filters_on_its_own():
    """Static check: only entitlements.py may touch the attributes a filter is made of."""
    offenders = []
    for path in RETRIEVAL_DIR.glob("*.py"):
        if path.name == "entitlements.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in GATE_ONLY:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
    assert not offenders, f"filtering outside entitlements.py: {offenders}"
