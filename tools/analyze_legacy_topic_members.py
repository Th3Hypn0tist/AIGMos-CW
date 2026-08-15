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
    docs = []
    for p in sorted(CANONICAL.rglob("*.json")):
        docs.append((p, json.loads(p.read_text(encoding="utf-8"))))
    return docs


def collect_declared_identities(docs):
    kinds: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[dict[str, str]]] = defaultdict(list)

    for p, d in docs:
        rel = str(p.relative_to(ROOT))
        identity = d.get("identity", {}) or {}
        if identity.get("id"):
            kinds[identity["id"]].add("contract")
            locations[identity["id"]].append({"kind": "contract", "file": rel})

        for e in d.get("entities", []) or []:
            eid = e.get("id")
            if eid:
                kinds[eid].add("entity")
                if e.get("entity_type_ref"):
                    kinds[eid].add(f"entity:{e['entity_type_ref']}")
                locations[eid].append({"kind": "entity", "file": rel})
            for prop in e.get("properties", []) or []:
                pid = prop.get("id")
                if pid:
                    kinds[pid].add("property")
                    if prop.get("property_type_ref"):
                        kinds[pid].add(f"property:{prop['property_type_ref']}")
                    locations[pid].append({"kind": "property", "file": rel})

        constraints = d.get("constraints", {}) or {}
        for inv in constraints.get("invariants", []) or []:
            iid = inv.get("id")
            if iid:
                kinds[iid].add("invariant")
                locations[iid].append({"kind": "invariant", "file": rel})
        for gate in constraints.get("hard_gates", []) or []:
            gid = gate.get("id")
            if gid:
                kinds[gid].add("hard_gate")
                locations[gid].append({"kind": "hard_gate", "file": rel})

        legacy = (d.get("metadata", {}) or {}).get("migration_legacy_non_authoritative", {}) or {}
        behavior = legacy.get("behavior", {}) or {}
        for field, kind in [
            ("operations", "operation"),
            ("events", "event"),
            ("flows", "flow"),
            ("states", "state"),
            ("interfaces", "interface"),
        ]:
            for obj in behavior.get(field, []) or []:
                oid = obj.get("id") if isinstance(obj, dict) else None
                if oid:
                    kinds[oid].add(kind)
                    locations[oid].append({"kind": kind, "file": rel})

        # Also support the direct legacy fields when present on main-style source docs.
        behavior2 = d.get("behavior", {}) or {}
        for field, kind in [
            ("operations", "operation"),
            ("events", "event"),
            ("flows", "flow"),
            ("states", "state"),
            ("interfaces", "interface"),
        ]:
            for obj in behavior2.get(field, []) or []:
                oid = obj.get("id") if isinstance(obj, dict) else None
                if oid:
                    kinds[oid].add(kind)
                    locations[oid].append({"kind": kind, "file": rel})

    return kinds, locations


def legacy_topics_for_doc(d: dict[str, Any]) -> list[dict[str, Any]]:
    meta = d.get("metadata", {}) or {}
    legacy = meta.get("migration_legacy_non_authoritative", {}) or {}
    topics = legacy.get("topics")
    if isinstance(topics, list):
        return topics

    # Some 2.0 migration artifacts preserve the entire old source under another key.
    for key in ("legacy_topics", "topics"):
        if isinstance(meta.get(key), list):
            return meta[key]
    return []


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
        topics = legacy_topics_for_doc(d)
        for ti, topic in enumerate(topics):
            topic_id = topic.get("id", f"<topic:{ti}>")
            for field in (
                "member_refs",
                "parent_topic_refs",
                "composed_topic_refs",
                "relation_refs",
                "operation_refs",
                "event_refs",
                "flow_refs",
                "child_topics",
            ):
                for ri, ref in enumerate(topic.get(field, []) or []):
                    if isinstance(ref, dict):
                        ref_id = ref.get("id") or ref.get("ref")
                    else:
                        ref_id = ref
                    if not ref_id:
                        continue
                    ks = sorted(kinds.get(ref_id, set()))
                    if not ks:
                        category = "unresolved"
                        unresolved.append({
                            "file": rel,
                            "topic_id": topic_id,
                            "field": field,
                            "index": ri,
                            "ref": ref_id,
                        })
                    else:
                        primary_order = [
                            "contract", "entity:topic", "invariant", "hard_gate",
                            "operation", "event", "flow", "state", "interface",
                            "property", "entity"
                        ]
                        category = next((x for x in primary_order if x in ks), ks[0])
                        if len(ks) > 1:
                            multi_kind.append({"ref": ref_id, "kinds": ks, "locations": locations.get(ref_id, [])})

                    category_counts[category] += 1
                    field_counts[field] += 1
                    records.append({
                        "file": rel,
                        "topic_id": topic_id,
                        "field": field,
                        "index": ri,
                        "ref": ref_id,
                        "resolved_kinds": ks,
                        "category": category,
                    })

    # Special view specifically for member_refs, which are the refs that produced most migration closure gaps.
    member_records = [r for r in records if r["field"] == "member_refs"]
    member_counts = Counter(r["category"] for r in member_records)

    report = {
        "analysis": "Legacy Topic reference semantic classification",
        "source": "preserved legacy Topic fields plus explicit identities in the canonical repository",
        "method": "Exact ID matching only; no semantic guessing from names.",
        "total_topic_reference_records": len(records),
        "total_member_refs": len(member_records),
        "all_reference_categories": dict(category_counts.most_common()),
        "member_ref_categories": dict(member_counts.most_common()),
        "field_counts": dict(field_counts.most_common()),
        "unresolved_count": len(unresolved),
        "unresolved_examples": unresolved[:500],
        "multi_kind_count": len(multi_kind),
        "multi_kind_examples": multi_kind[:200],
        "member_ref_examples_by_category": {},
    }

    bycat = defaultdict(list)
    for r in member_records:
        if len(bycat[r["category"]]) < 50:
            bycat[r["category"]].append(r)
    report["member_ref_examples_by_category"] = dict(bycat)

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
