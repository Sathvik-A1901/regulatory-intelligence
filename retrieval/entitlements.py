"""Policy decision point: the only place a retrieval filter can be built.

Design rule: no code path constructs a retrieval query without going through this
module. It is enforced three ways, each covered by tests/test_entitlements.py:

  1. Retrievers accept only a RetrievalFilter, never a bare date or role.
  2. A RetrievalFilter cannot be constructed outside this module.
  3. No other module in retrieval/ reads access_class or validity dates directly.

The filter is applied inside the query, before ranking. The model is never asked
to enforce access: a passage the asker may not read never reaches the prompt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from ingest.metadata import Chunk

DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "config" / "entitlements.yaml"
audit = logging.getLogger("regintel.audit")

_SEAL = object()  # only this module holds it


@dataclass(frozen=True)
class RetrievalFilter:
    role: str
    allowed_classes: frozenset[str]
    as_of: date | None  # None only in the date-ablation filter below
    _seal: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("RetrievalFilter must be built by retrieval.entitlements")

    def admits(self, chunk: Chunk) -> bool:
        if chunk.access_class.value not in self.allowed_classes:
            return False
        return self.as_of is None or chunk.in_force_on(self.as_of)


class Policy:
    def __init__(self, path: Path = DEFAULT_POLICY):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.roles: dict[str, frozenset[str]] = {r: frozenset(c) for r, c in cfg["roles"].items()}


_default_policy: Policy | None = None


def _policy(policy: Policy | None) -> Policy:
    global _default_policy
    if policy is not None:
        return policy
    if _default_policy is None:
        _default_policy = Policy()
    return _default_policy


def _decide(role: str, as_of: date | None, policy: Policy | None, purpose: str) -> RetrievalFilter:
    classes = _policy(policy).roles.get(role)
    allowed = classes is not None
    # Unknown role: deny everything rather than fall back to some default.
    audit.info(json.dumps({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "decision": "allow" if allowed else "deny",
        "role": role,
        "classes": sorted(classes or []),
        "as_of": as_of.isoformat() if as_of else None,
        "purpose": purpose,
    }))
    return RetrievalFilter(role=role, allowed_classes=classes or frozenset(), as_of=as_of, _seal=_SEAL)


def build_filter(role: str, as_of: date, policy: Policy | None = None) -> RetrievalFilter:
    """The filter for a real query: entitlements and the as-of date, both enforced."""
    if as_of is None:
        raise ValueError("as_of is required; use date.today() for current law")
    return _decide(role, as_of, policy, "query")


def ablation_filter_ignoring_dates(role: str, policy: Policy | None = None) -> RetrievalFilter:
    """Eval-only: drops the as-of filter to measure what it buys. Entitlements still apply."""
    return _decide(role, None, policy, "eval-ablation")
