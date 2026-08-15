#!/usr/bin/env python3
"""Remove unresolved migrated Link authority without guessing.

A CCF 2.0 Link endpoint must resolve to an Entity or Property identity. Legacy
AIGMos Topics also referenced constraints, gates and other non-Entity/non-Property
identities. Those references are useful evidence but are not valid CCF 2.0 Link
endpoints unless explicitly remodeled.

This pass therefore:
- builds the complete Entity + Property canonical identity namespace;
- keeps only Link Properties whose parent_ref and child_ref both resolve;
- moves unresolved Link candidates into non-authoritative migration evidence;
- adds explicit required Gaps describing what was not promoted.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"


def main() -> int:
    docs: list[tuple[Path, dict]] = []
    ids: set[str] = set()

    for p in sorted(CANONICAL.rglob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        docs.append((p, d))
        for e in d.get("entities", []) or []:
            if e.get("id"):
                ids.add(e["id"])
            for prop in e.get("properties", []) or []:
                if prop.get("id"):
                    ids.add(prop["id"])

    removed = 0
    affected_files = 0

    for p, d in docs:
        changed = False
        metadata = d.setdefault("metadata", {})
        unresolved_evidence = metadata.setdefault("migration_unresolved_links", [])
        gaps = d.setdefault("gaps", [])
        existing_gap_ids = {g.get("gap_id") for g in gaps}

        for e in d.get("entities", []) or []:
            kept = []
            for prop in e.get("properties", []) or []:
                if prop.get("property_type_ref") != "link":
                    kept.append(prop)
                    continue
                v = prop.get("value", {}) or {}
                parent = v.get("parent_ref")
                child = v.get("child_ref")
                missing = []
                if parent not in ids:
                    missing.append({"endpoint": "parent_ref", "ref": parent})
                if child not in ids:
                    missing.append({"endpoint": "child_ref", "ref": child})
                if not missing:
                    kept.append(prop)
                    continue

                unresolved_evidence.append({
                    "property": prop,
                    "missing_endpoints": missing,
                    "rule": "Not promoted as active Link because CCF 2.0 endpoint closure failed; no target identity was invented."
                })
                gid = f"MIG20_UNRESOLVED_LINK::{prop.get('id')}"
                if gid not in existing_gap_ids:
                    gaps.append({
                        "gap_id": gid,
                        "type": "unresolved_link_endpoint",
                        "status": "open",
                        "severity": "required",
                        "entity_ref": e.get("id"),
                        "property_ref": prop.get("id"),
                        "description": "Legacy directed reference could not be promoted to an active CCF 2.0 Link because one or more endpoints do not resolve to an Entity or Property identity.",
                        "metadata": {"missing_endpoints": missing}
                    })
                    existing_gap_ids.add(gid)
                removed += 1
                changed = True
            e["properties"] = kept

        if not unresolved_evidence:
            metadata.pop("migration_unresolved_links", None)
        if changed:
            affected_files += 1
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "operation": "CCF 2.0 Link endpoint closure cleanup",
        "canonical_identity_count": len(ids),
        "removed_unresolved_link_properties": removed,
        "affected_files": affected_files,
        "rule": "Unresolved legacy Link candidates are retained only as non-authoritative evidence plus explicit Gaps; no endpoint identity is guessed."
    }
    (ROOT / "cw20-link-closure-cleanup-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
