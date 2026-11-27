"""eCFR versioner API client.

Docs: https://www.ecfr.gov/reader-aids/ecfr-developer-resources/rest-api-interactive-documentation
No key needed. Point-in-time content is available from 2017-01-03.

Two endpoints do all the work:
  /api/versioner/v1/versions/title-{t}.json?part=P   -> every dated version of every unit in a part
  /api/versioner/v1/full/{date}/title-{t}.xml?part=P&section=S  -> the XML of one unit on one date
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ingest.sources.http import PoliteClient

BASE = "https://www.ecfr.gov/api/versioner/v1"


@dataclass(frozen=True)
class UnitVersion:
    identifier: str  # '1026.19', 'Appendix H to Part 1026', 'Supplement I to Part 1026'
    name: str
    type: str  # 'section' | 'appendix'
    part: str
    subpart: str | None
    date: date  # date this version took effect in eCFR
    amendment_date: date | None
    issue_date: date | None
    substantive: bool
    removed: bool


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class ECFRClient:
    def __init__(self, cache_dir: Path):
        self.http = PoliteClient(cache_dir / "ecfr")

    def versions(self, title: int, part: str, refresh: bool = True) -> list[UnitVersion]:
        """All dated versions of every section and appendix in a part.

        Always refreshed by default: this small index is what tells an incremental
        run which (unit, date) pairs are new.
        """
        body = self.http.get_text(
            f"{BASE}/versions/title-{title}.json", {"part": part}, ".json", use_cache=not refresh
        )
        if body is None:
            raise LookupError(f"No versions for title {title} part {part}")
        rows = json.loads(body).get("content_versions", [])
        out = []
        for r in rows:
            d = _d(r.get("date")) or _d(r.get("amendment_date"))
            if d is None:
                continue
            out.append(
                UnitVersion(
                    identifier=r["identifier"],
                    name=r.get("name", r["identifier"]),
                    type=r.get("type", "section"),
                    part=str(r.get("part", part)),
                    subpart=r.get("subpart"),
                    date=d,
                    amendment_date=_d(r.get("amendment_date")),
                    issue_date=_d(r.get("issue_date")),
                    substantive=bool(r.get("substantive", True)),
                    removed=bool(r.get("removed", False)),
                )
            )
        return out

    def unit_xml(self, title: int, part: str, unit: UnitVersion, on: date) -> str | None:
        """XML of one section or appendix as it read on a date. Cached forever: history is fixed."""
        params = {"part": part}
        if unit.type == "appendix":
            params["appendix"] = unit.identifier
        else:
            params["section"] = unit.identifier
        return self.http.get_text(f"{BASE}/full/{on.isoformat()}/title-{title}.xml", params, ".xml")

    def close(self) -> None:
        self.http.close()
