"""Ingestion pipeline: eCFR versions -> parsed units -> interval-tagged chunks.

Writes to data/processed/:
  chunks.jsonl         one Chunk per line, the thing that gets indexed
  units/<id>.json      every parsed version of every unit (the parent documents)
  manifest.json        what was ingested, per-unit version dates, and what changed since last run
  parse_report.json    parse-quality signals to inspect by hand before trusting the corpus
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from ingest.chunk import assign_intervals, chunk_versions
from ingest.metadata import AccessClass, Chunk, DocKind, SectionVersion
from ingest.parse import parse_unit
from ingest.sources.ecfr import ECFRClient, UnitVersion
from ingest.sources.federal_register import FederalRegisterClient, rules_effective_on


@dataclass
class UnitReport:
    identifier: str
    kind: str
    versions: int
    blocks_latest: int
    tables_latest: int
    unmarked_blocks_latest: int  # regulation blocks with no paragraph path beyond the intro
    fetch_failures: list[str] = field(default_factory=list)


def _slug(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", identifier).strip("_")


def load_corpus_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def ingest_part(
    title: int,
    part: str,
    since: date,
    ecfr: ECFRClient,
    fr: FederalRegisterClient | None,
    regulator: str,
    jurisdiction: str,
    only: list[str] | None = None,
    log=print,
) -> tuple[list[Chunk], list[SectionVersion], list[UnitReport]]:
    rows = ecfr.versions(title, part)
    by_unit: dict[str, list[UnitVersion]] = defaultdict(list)
    for r in rows:
        if only and r.identifier not in only:
            continue
        by_unit[r.identifier].append(r)

    rules = fr.final_rules(title, part, since) if fr else []
    log(f"  {title} CFR {part}: {len(by_unit)} units, {len(rows)} version rows, {len(rules)} FR rules")

    chunks: list[Chunk] = []
    all_versions: list[SectionVersion] = []
    reports: list[UnitReport] = []

    for ident, uvs in sorted(by_unit.items()):
        uvs.sort(key=lambda u: u.date)
        # Versions dated before the window collapse onto the window start: that text was in force then.
        dates = sorted({max(u.date, since) for u in uvs})
        intervals = assign_intervals(dates)
        removed_on = {max(u.date, since) for u in uvs if u.removed}
        meta = {max(u.date, since): u for u in uvs}
        failures: list[str] = []
        versions: list[SectionVersion] = []

        for start, end in intervals:
            u = meta[start]
            if start in removed_on:
                versions.append(SectionVersion(
                    title=title, part=part, identifier=ident, heading=u.name, kind=DocKind.REGULATION,
                    valid_from=start, valid_to=end, removed=True, substantive=u.substantive,
                ))
                continue
            xml = ecfr.unit_xml(title, part, u, start)
            if xml is None:
                failures.append(start.isoformat())
                continue
            try:
                heading, kind, blocks = parse_unit(xml, ident, u.type)
            except Exception as exc:  # keep going; the report shows what broke
                failures.append(f"{start.isoformat()}: parse error {exc!r}")
                continue
            versions.append(SectionVersion(
                title=title, part=part, identifier=ident, heading=heading or u.name, kind=kind,
                valid_from=start, valid_to=end, substantive=u.substantive,
                amending_documents=rules_effective_on(rules, start), blocks=blocks,
            ))

        live = [v for v in versions if not v.removed]
        unit_chunks = chunk_versions(versions, AccessClass.PUBLIC, regulator, jurisdiction)
        chunks.extend(unit_chunks)
        all_versions.extend(versions)

        latest = live[-1] if live else None
        reports.append(UnitReport(
            identifier=ident,
            kind=latest.kind.value if latest else "removed",
            versions=len(versions),
            blocks_latest=len(latest.blocks) if latest else 0,
            tables_latest=sum(b.is_table for b in latest.blocks) if latest else 0,
            unmarked_blocks_latest=(
                sum(1 for b in latest.blocks[1:] if not b.path)
                if latest and latest.kind is DocKind.REGULATION else 0
            ),
            fetch_failures=failures,
        ))
        log(f"    {ident:<40} {len(versions):>3} versions  {len(unit_chunks):>5} chunks"
            + (f"  [{len(failures)} failures]" if failures else ""))
    return chunks, all_versions, reports


def run_ingest(config_path: Path, data_dir: Path, only: list[str] | None = None,
               with_federal_register: bool = True, log=print) -> dict:
    cfg = load_corpus_config(config_path)
    since = date.fromisoformat(str(cfg["since"]))
    raw, out = data_dir / "raw", data_dir / "processed"
    (out / "units").mkdir(parents=True, exist_ok=True)

    previous = {}
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("units", {})

    ecfr = ECFRClient(raw)
    fr = FederalRegisterClient(raw) if with_federal_register else None
    all_chunks: list[Chunk] = []
    all_reports: list[UnitReport] = []
    units_manifest: dict[str, dict] = {}
    try:
        for p in cfg["parts"]:
            log(f"Ingesting {p['name']}")
            chunks, versions, reports = ingest_part(
                int(p["title"]), str(p["part"]), since, ecfr, fr,
                cfg.get("regulator", "CFPB"), cfg.get("jurisdiction", "US-federal"), only, log,
            )
            all_chunks += chunks
            all_reports += reports
            grouped: dict[str, list[SectionVersion]] = defaultdict(list)
            for v in versions:
                grouped[v.identifier].append(v)
            for ident, vs in grouped.items():
                key = f"{p['title']}cfr{ident}"
                (out / "units" / f"{_slug(key)}.json").write_text(
                    json.dumps([v.model_dump(mode="json") for v in vs], indent=1), encoding="utf-8")
                units_manifest[key] = {"dates": [v.valid_from.isoformat() for v in vs]}
    finally:
        ecfr.close()
        if fr:
            fr.close()

    with (out / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for c in all_chunks:
            fh.write(c.model_dump_json() + "\n")

    changed = sorted(k for k, v in units_manifest.items() if previous.get(k) != v)
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "since": since.isoformat(),
        "chunks": len(all_chunks),
        "chunks_in_force_today": sum(c.valid_to is None for c in all_chunks),
        "units": units_manifest,
        "changed_since_last_run": changed,
        "http": {"ecfr_cache_hits": ecfr.http.hits, "ecfr_fetched": ecfr.http.misses},
    }
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (out / "parse_report.json").write_text(
        json.dumps([r.__dict__ for r in all_reports], indent=1), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    """python -m ingest.flow [--only 1026.19 --only 1026.43] [--no-fr]"""
    import argparse

    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Ingest eCFR versions into data/processed/")
    ap.add_argument("--config", type=Path, default=root / "config" / "corpus.yaml")
    ap.add_argument("--data", type=Path, default=root / "data")
    ap.add_argument("--only", action="append", help="Limit to a unit identifier; repeatable")
    ap.add_argument("--no-fr", action="store_true", help="Skip the Federal Register lookup")
    args = ap.parse_args(argv)

    m = run_ingest(args.config, args.data, only=args.only, with_federal_register=not args.no_fr)
    print(f"\n{m['chunks']} chunks ({m['chunks_in_force_today']} in force today); "
          f"{len(m['changed_since_last_run'])} units changed since last run; "
          f"fetched {m['http']['ecfr_fetched']}, cache hits {m['http']['ecfr_cache_hits']}")

    report = json.loads((args.data / "processed" / "parse_report.json").read_text(encoding="utf-8"))
    suspect = [r for r in report if r["fetch_failures"] or r["unmarked_blocks_latest"] > 2]
    print(f"units to inspect by hand: {len(suspect)}")
    for r in suspect[:20]:
        print(f"  {r['identifier']:<40} unmarked={r['unmarked_blocks_latest']} "
              f"failures={r['fetch_failures'][:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
