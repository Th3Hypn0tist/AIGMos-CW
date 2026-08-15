#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"
REPORT = ROOT / "cw20-legacy-topic-member-analysis.json"


def load_docs() -> list[tuple[Path, dict[str, Any]]]:
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(CANONICAL.rglob("*.json"))]


def add_identity(kinds, locations, ident, kind, rel):
    if not ident:
        return
    kinds[ident].add(kind)
    locations[ident].append({"kind": kind, "file": rel})


def collect_declared_identities(docs):
    kinds: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[dict[str, str]]] = defaultdict(list)

    for p, d in docs:
        rel = str(p.relative_to(ROOT))
        identity = d.get("identity", {}) or {}
        add_identity(kinds, locations, identity.get("id"), "contract", rel)

        for e in d.get("entities", []) or []:
            eid = e.get("id")
            add_identity(kinds, locations, eid, "entity", rel)
            if eid and e.get("entity_type_ref"):
                add_identity(kinds, locations, eid, f"entity:{e['entity_type_ref']}", rel)
            for prop in e.get("properties", []) or []:
                pid = prop.get("id")
                add_identity(kinds, locations, pid, "property", rel)
                if pid and prop.get("property_type_ref"):
                    add_identity(kinds, locations, pid, f"property:{prop['property_type_ref']}", rel)

        constraints = d.get("constraints", {}) or {}
        for inv in constraints.get("invariants", []) or []:
            add_identity(kinds, locations, inv.get("id"), "invariant", rel)
        for gate in constraints.get("hard_gates", []) or []:
            add_identity(kinds, locations, gate.get("id"), "hard_gate", rel)

        legacy = (d.get("metadata", {}) or {}).get("migration_legacy_non_authoritative", {}) or {}

        # Legacy members[] was itself an explicit identity table. Classify it exactly by declared type.
        # This catches flow symbols and relation symbols that were Topic members but are not CCF 2.0 Entities/Properties.
        for member in legacy.get("members", []) or []:
            if not isinstance(member, dict):
                continue
            mid = member.get("id")
            mtype = member.get("type") or "unspecified"
            add_identity(kinds, locations, mid, "legacy_member", rel)
            add_identity(kinds, locations, mid, f"legacy_member:{mtype}", rel)

        behavior = legacy.get("behavior", {}) or {}
        for field, kind in [
            ("operations", "operation"),
            ("events", "event"),
            ("flows", "flow"),
            ("states", "state"),
            ("interfaces", "interface"),
        ]:
            for obj in behavior.get(field, []) or []:
                add_identity(kinds, locations, obj.get("id") if isinstance(obj, dict) else None, kind, rel)

        # Also support direct legacy fields if the analyzer is run against a legacy source tree.
        for member in d.get("members", []) or []:
            if not isinstance(member, dict):
                continue
            mid = member.get("id")
            mtype = member.get("type") or "unspecified"
            add_identity(kinds, locations, mid, "legacy_member", rel)
            add_identity(kinds, locations, mid, f"legacy_member:{mtype}", rel)

        behavior2 = d.get("behavior", {}) or {}
        for field, kind in [
            ("operations", "operation"),
            ("events", "event"),
            ("flows", "flow"),
            ("states", "state"),
            ("interfaces", "interface"),
        ]:
            for obj in behavior2.get(field, []) or []:
                add_identity(kinds, locations, obj.get("id") if isinstance(obj, dict) else None, kind, rel)

    return kinds, locations


def legacy_topics_for_doc(d: dict[str, Any]) -> list[dict[str, Any]]:
    meta = d.get("metadata", {}) or {}
    legacy = meta.get("migration_legacy_non_authoritative", {}) or {}
    topics = legacy.get("topics")
    if isinstance(topics, list):
        return topics
    for key in ("legacy_topics", "topics"):
        if isinstance(meta.get(key), list):
            return meta[key]
    return []


def choose_category(kinds: list[str]) -> str:
    primary_order = [
        "contract", "entity:topic", "invariant", "hard_gate",
        "operation", "event", "flow", "state", "interface",
        "property", "entity"
    ]
    for k in primary_order:
        if k in kinds:
            return k
    legacy_typed = sorted(k for k in kinds if k.startswith("legacy_member:"))
    if legacy_typed:
        return legacy_typed[0]
    if "legacy_member" in kinds:
        return "legacy_member"
    return kinds[0]


def main() -> int:
    docs = load_docs()
    kinds, locations = collect_declared_identities(docs)

    records = []
    category_counts = Counter()
    field_counts = Counter()
    unresolved = []
    multi_kind = []

    for p, d in docs:
        rel = str(p.relative_to(ROOT))
        for ti, topic in enumerate(legacy_topics_for_doc(d)):
            topic_id = topic.get("id", f"<topic:{ti}>")
            for field in (
                "member_refs", "parent_topic_refs", "composed_topic_refs",
                "relation_refs", "operation_refs", "event_refs", "flow_refs", "child_topics",
            ):
                for ri, ref in enumerate(topic.get(field, []) or []):
                    ref_id = (ref.get("id") or ref.get("ref")) if isinstance(ref, dict) else ref
                    if not ref_id:
                        continue
                    ks = sorted(kinds.get(ref_id, set()))
                    if not ks:
                        category = "unresolved"
                        unresolved.append({"file": rel, "topic_id": topic_id, "field": field, "index": ri, "ref": ref_id})
                    else:
                        category = choose_category(ks)
                        if len(ks) > 1:
                            multi_kind.append({"ref": ref_id, "kinds": ks, "locations": locations.get(ref_id, [])})
                    category_counts[category] += 1
                    field_counts[field] += 1
                    records.append({
                        "file": rel, "topic_id": topic_id, "field": field, "index": ri,
                        "ref": ref_id, "resolved_kinds": ks, "category": category,
                    })

    member_records = [r for r in records if r["field"] == "member_refs"]
    member_counts = Counter(r["category"] for r in member_records)

    bycat = defaultdict(list)
    for r in member_records:
        if len(bycat[r["category"]]) < 100:
            bycat[r["category"]].append(r)

    report = {
        "analysis": "Legacy Topic reference semantic classification",
        "source": "preserved legacy Topic fields, legacy members[], behavior identities, constraints and current canonical identities",
        "method": "Exact ID matching and declared legacy type only; no semantic guessing from names.",
        "total_topic_reference_records": len(records),
        "total_member_refs": len(member_records),
        "all_reference_categories": dict(category_counts.most_common()),
        "member_ref_categories": dict(member_counts.most_common()),
        "field_counts": dict(field_counts.most_common()),
        "unresolved_count": len(unresolved),
        "unresolved_examples": unresolved[:1000],
        "multi_kind_count": len(multi_kind),
        "multi_kind_examples": multi_kind[:300],
        "member_ref_examples_by_category": dict(bycat),
    }

    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "total_member_refs": len(member_records),
        "member_ref_categories": dict(member_counts.most_common()),
        "unresolved_count": len(unresolved),
        "result": "ok",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
