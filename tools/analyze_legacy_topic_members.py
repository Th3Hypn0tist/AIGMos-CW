#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"
REPORT = ROOT / "cw20-legacy-topic-member-analysis.json"


def sid(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "UNKNOWN"))


def qid(owner: str, kind: str, local: Any) -> str:
    return f"{sid(owner)}::{kind}::{sid(local)}"


def load_docs() -> list[tuple[Path, dict[str, Any]]]:
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(CANONICAL.rglob("*.json"))]


def legacy_for_doc(d: dict[str, Any]) -> dict[str, Any]:
    return ((d.get("metadata", {}) or {}).get("migration_legacy_non_authoritative", {}) or {})


def legacy_topics_for_doc(d: dict[str, Any]) -> list[dict[str, Any]]:
    legacy = legacy_for_doc(d)
    if isinstance(legacy.get("topics"), list):
        return legacy["topics"]
    return []


def legacy_behavior_for_doc(d: dict[str, Any]) -> dict[str, Any]:
    legacy = legacy_for_doc(d)
    return legacy.get("behavior", {}) or {}


def local_index(d: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index only identities declared by the owning legacy contract.

    Legacy Topic member_refs were owner-scoped trace membership. Repeated ids such as
    GATE_1 therefore MUST be resolved against the same contract before any global lookup.
    """
    owner = (d.get("identity", {}) or {}).get("id", "UNKNOWN")
    legacy = legacy_for_doc(d)
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)

    constraints = d.get("constraints", {}) or {}
    for inv in constraints.get("invariants", []) or []:
        if inv.get("id"):
            idx[inv["id"]].append({
                "kind": "invariant",
                "canonical_target_ref": qid(owner, "RULE::INVARIANT", inv["id"]),
            })
    for gate in constraints.get("hard_gates", []) or []:
        if gate.get("id"):
            idx[gate["id"]].append({
                "kind": "hard_gate",
                "canonical_target_ref": qid(owner, "RULE::HARD_GATE", gate["id"]),
            })

    for member in legacy.get("members", []) or []:
        if not isinstance(member, dict) or not member.get("id"):
            continue
        mtype = member.get("type") or "unspecified"
        idx[member["id"]].append({
            "kind": f"legacy_member:{mtype}",
            "canonical_target_ref": qid(owner, "FLOW_SYMBOL", member["id"]),
        })

    behavior = legacy_behavior_for_doc(d)
    behavior_map = {
        "operations": ("operation", "OPERATIONS"),
        "events": ("event", "EVENT"),
        "flows": ("flow", "FLOWS"),
        "states": ("state", "STATES"),
        "interfaces": ("interface", "INTERFACES"),
    }
    for field, (kind, qkind) in behavior_map.items():
        for obj in behavior.get(field, []) or []:
            if isinstance(obj, dict) and obj.get("id"):
                idx[obj["id"]].append({
                    "kind": kind,
                    "canonical_target_ref": qid(owner, qkind, obj["id"]),
                })

    structure = legacy.get("structure", {}) or {}
    for field in ("containment", "relations", "ownership", "authority", "dependencies"):
        for i, edge in enumerate(structure.get(field, []) or []):
            if not isinstance(edge, dict):
                continue
            edge_id = edge.get("id")
            if edge_id:
                idx[edge_id].append({
                    "kind": f"structural_link:{field}",
                    "canonical_target_ref": qid(owner, "LINK", edge_id),
                })

    return idx


def global_contract_and_topic_index(docs: list[tuple[Path, dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p, d in docs:
        rel = str(p.relative_to(ROOT))
        cid = (d.get("identity", {}) or {}).get("id")
        if cid:
            idx[cid].append({"kind": "contract", "canonical_target_ref": cid, "file": rel})
        for e in d.get("entities", []) or []:
            if e.get("id") and e.get("entity_type_ref") == "topic":
                idx[e["id"]].append({"kind": "topic", "canonical_target_ref": e["id"], "file": rel})
    return idx


def resolve_member(ref_id: str, local: dict[str, list[dict[str, Any]]], global_idx: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    candidates = local.get(ref_id, [])
    if len(candidates) == 1:
        return {"resolution": "local_exact", **candidates[0]}
    if len(candidates) > 1:
        return {"resolution": "ambiguous_local", "kind": "ambiguous", "candidates": candidates}

    globals_ = global_idx.get(ref_id, [])
    unique = {(x["kind"], x["canonical_target_ref"]) for x in globals_}
    if len(unique) == 1:
        kind, target = next(iter(unique))
        return {"resolution": "global_exact", "kind": kind, "canonical_target_ref": target}
    if len(unique) > 1:
        return {"resolution": "ambiguous_global", "kind": "ambiguous", "candidates": globals_}
    return {"resolution": "unresolved", "kind": "unresolved"}


def main() -> int:
    docs = load_docs()
    global_idx = global_contract_and_topic_index(docs)

    records: list[dict[str, Any]] = []
    member_counts = Counter()
    resolution_counts = Counter()
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    field_counts = Counter()

    for p, d in docs:
        rel = str(p.relative_to(ROOT))
        owner = (d.get("identity", {}) or {}).get("id", p.stem)
        local = local_index(d)
        for ti, topic in enumerate(legacy_topics_for_doc(d)):
            topic_id = topic.get("id", f"<topic:{ti}>")
            for field in (
                "member_refs", "parent_topic_refs", "composed_topic_refs",
                "relation_refs", "operation_refs", "event_refs", "flow_refs", "child_topics",
            ):
                for ri, raw in enumerate(topic.get(field, []) or []):
                    ref_id = (raw.get("id") or raw.get("ref")) if isinstance(raw, dict) else raw
                    if not ref_id:
                        continue
                    field_counts[field] += 1
                    if field == "member_refs":
                        resolved = resolve_member(str(ref_id), local, global_idx)
                    else:
                        # Non-member Topic fields retain their own explicit field semantics.
                        # Classify only by exact global/local identity; do not reinterpret them as member semantics.
                        resolved = resolve_member(str(ref_id), local, global_idx)
                    record = {
                        "file": rel,
                        "owner_contract_ref": owner,
                        "topic_id": topic_id,
                        "field": field,
                        "index": ri,
                        "ref": ref_id,
                        **resolved,
                    }
                    records.append(record)
                    resolution_counts[resolved["resolution"]] += 1
                    if field == "member_refs":
                        member_counts[resolved["kind"]] += 1
                    if resolved["resolution"] == "unresolved":
                        unresolved.append(record)
                    elif resolved["resolution"].startswith("ambiguous"):
                        ambiguous.append(record)

    member_records = [r for r in records if r["field"] == "member_refs"]
    member_unresolved = [r for r in member_records if r["resolution"] == "unresolved"]
    member_ambiguous = [r for r in member_records if r["resolution"].startswith("ambiguous")]

    bycat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in member_records:
        if len(bycat[r["kind"]]) < 100:
            bycat[r["kind"]].append(r)

    report = {
        "analysis": "Legacy Topic reference owner-scoped semantic classification",
        "source": "preserved legacy Topic fields plus declarations in each owning contract",
        "method": (
            "Resolve Topic member_refs against declarations in the same legacy contract first; "
            "only then resolve globally unique contract/topic identities. No name-based semantic guessing."
        ),
        "total_topic_reference_records": len(records),
        "total_member_refs": len(member_records),
        "member_ref_categories": dict(member_counts.most_common()),
        "resolution_counts": dict(resolution_counts.most_common()),
        "field_counts": dict(field_counts.most_common()),
        "member_unresolved_count": len(member_unresolved),
        "member_ambiguous_count": len(member_ambiguous),
        "all_unresolved_count": len(unresolved),
        "all_ambiguous_count": len(ambiguous),
        "member_unresolved_examples": member_unresolved[:500],
        "member_ambiguous_examples": member_ambiguous[:500],
        "all_unresolved_examples": unresolved[:500],
        "member_ref_examples_by_category": dict(bycat),
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "total_member_refs": len(member_records),
        "member_ref_categories": dict(member_counts.most_common()),
        "member_unresolved_count": len(member_unresolved),
        "member_ambiguous_count": len(member_ambiguous),
        "result": "ok",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
