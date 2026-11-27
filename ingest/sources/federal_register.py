"""Federal Register API client: where supersession comes from.

Each final rule that amended a CFR part carries an effective date. Matching those
to eCFR version dates lets every chunk name the rule document that put it in force.
Docs: https://www.federalregister.gov/reader-aids/developer-resources/rest-api
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ingest.sources.http import PoliteClient

BASE = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = ["document_number", "citation", "title", "effective_on", "publication_date", "html_url"]


@dataclass(frozen=True)
class RuleDocument:
    document_number: str
    citation: str | None  # e.g. '85 FR 86308'
    title: str
    effective_on: date | None
    publication_date: date
    html_url: str


class FederalRegisterClient:
    def __init__(self, cache_dir: Path):
        self.http = PoliteClient(cache_dir / "federal_register", min_interval=0.5)

    def final_rules(self, title: int, part: str, since: date) -> list[RuleDocument]:
        docs: list[RuleDocument] = []
        page = 1
        while True:
            params: dict = {
                "conditions[cfr][title]": title,
                "conditions[cfr][part]": part,
                "conditions[type][]": "RULE",
                "conditions[publication_date][gte]": since.isoformat(),
                "order": "oldest",
                "per_page": 200,
                "page": page,
                "fields[]": FIELDS,  # httpx sends a list as repeated keys
            }
            body = self.http.get_text(BASE, params, ".json", use_cache=False)
            if body is None:
                break
            payload = json.loads(body)
            for r in payload.get("results", []):
                docs.append(
                    RuleDocument(
                        document_number=r["document_number"],
                        citation=r.get("citation"),
                        title=r.get("title", ""),
                        effective_on=date.fromisoformat(r["effective_on"]) if r.get("effective_on") else None,
                        publication_date=date.fromisoformat(r["publication_date"]),
                        html_url=r.get("html_url", ""),
                    )
                )
            if page >= int(payload.get("total_pages", 1) or 1):
                break
            page += 1
        return docs

    def close(self) -> None:
        self.http.close()


def rules_effective_on(rules: list[RuleDocument], on: date) -> list[str]:
    """Citations of rules whose effective date equals an eCFR version date."""
    return [r.citation or r.document_number for r in rules if r.effective_on == on]
