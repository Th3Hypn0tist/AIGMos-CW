#!/usr/bin/env python3
"""Deterministic AIGMos Canonical Contract 1.4 -> CW 1.6 migration.

This migration performs ONLY transformations explicitly defined by
Canonical Contract Format 1.6. Unknown or underspecified semantics are
preserved as non-authoritative migration metadata and surfaced as gaps.

The migration deliberately unlocks affected contracts. It does not claim
1.6 lock eligibility; architecture placement/profile work and repository-wide
closure validation must happen after serialization migration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"

CORE_TYPES = {
    "containment",
    "relation",
    "ownership",
    "authority",
    "dependency",
    "topic_parent",
    "topic_member",
    "topic_composition",
}


def link(link_id: str, type_ref: str, source_ref: str, target_ref: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": link_id,
        "type_ref": type_ref,
        "source_ref": source_ref,
        "target_ref": target_ref,
        "status": "unlocked",
        "properties": properties,
        "maturity": "typed",
    }


def append_gap(gaps: list[dict[str, Any]], *, gap_id: str, gap_type: str, description: str,
               target_ref: str | None = None, source_ref: str | None = None,
               field_path: str | None = None, severity: str = "blocking") -> None:
    g: dict[str, Any] = {
        "gap_id": gap_id,
        "type": gap_type,
        "status": "open",
        "severity": severity,
        "description": description,
    }
    if target_ref is not None:
        g["target_ref"] = target_ref
    if source_ref is not None:
        g["source_ref"] = source_ref
    if field_path is not None:
        g["field_path"] = field_path
    gaps.append(g)


def convert_structure(structure: dict[str, Any], links: list[dict[str, Any]]) -> None:
    for e in structure.get("containment", []) or []:
        links.append(link(e["id"], "containment", e["parent_ref"], e["child_ref"], {
            "relation_type": e["relation_type"],
        }))

    for e in structure.get("relations", []) or []:
        links.append(link(e["id"], "relation", e["source_ref"], e["target_ref"], {
            "relation_type": e["relation_type"],
            "direction": e["direction"],
        }))

    for e in structure.get("ownership", []) or []:
        links.append(link(e["id"], "ownership", e["owner_ref"], e["target_ref"], {
            "ownership_type": e["ownership_type"],
        }))

    for e in structure.get("authority", []) or []:
        links.append(link(e["id"], "authority", e["authority_ref"], e["target_ref"], {
            "authority_type": e["authority_type"],
            "scope": e["scope"],
        }))

    for e in structure.get("dependencies", []) or []:
        links.append(link(e["id"], "dependency", e["source_ref"], e["target_ref"], {
            "dependency_type": e["dependency_type"],
            "required": e["required"],
        }))


def ensure_topic_member(topic: dict[str, Any], members: list[dict[str, Any]]) -> None:
    tid = topic.get("id")
    if not tid:
        return
    if any(m.get("id") == tid for m in members):
        return
    members.append({
        "id": tid,
        "name": topic.get("name", tid),
        "type": "topic",
        "status": "unlocked",
        "semantics": {
            "purpose": topic.get("purpose", ""),
            "metadata": topic.get("metadata", {}),
        },
    })


def convert_topics(topics: list[dict[str, Any]], members: list[dict[str, Any]],
                   links: list[dict[str, Any]], gaps: list[dict[str, Any]], contract_id: str) -> None:
    for ti, t in enumerate(topics):
        tid = t.get("id")
        if not tid:
            append_gap(
                gaps,
                gap_id=f"MIG16_TOPIC_ID_{ti}",
                gap_type="missing_topic_identity",
                description="Legacy Topic has no stable id and cannot be migrated deterministically.",
                field_path=f"topics[{ti}]",
            )
            continue

        ensure_topic_member(t, members)

        for i, parent in enumerate(t.get("parent_topic_refs", []) or []):
            links.append(link(f"MIG16_TOPIC_PARENT_{tid}_{i}", "topic_parent", tid, parent, {}))

        for i, member_ref in enumerate(t.get("member_refs", []) or []):
            links.append(link(f"MIG16_TOPIC_MEMBER_{tid}_{i}", "topic_member", tid, member_ref, {}))

        for i, component in enumerate(t.get("composed_topic_refs", []) or []):
            links.append(link(f"MIG16_TOPIC_COMPOSE_{tid}_{i}", "topic_composition", tid, component, {}))

        # 1.6 provides explicit mappings for parent_topic_refs, member_refs and
        # composed_topic_refs. The following legacy Topic trace dimensions do
        # not have a normative 1.6 migration mapping, so preserve uncertainty.
        for field in ("relation_refs", "operation_refs", "event_refs", "flow_refs", "child_topics"):
            values = t.get(field, []) or []
            if values:
                append_gap(
                    gaps,
                    gap_id=f"MIG16_TOPIC_{field.upper()}_{tid}",
                    gap_type="legacy_topic_trace_mapping_unresolved",
                    description=(
                        f"Legacy Topic field {field} contains identity connections but Canonical Contract "
                        "Format 1.6 does not define a normative migration Link Type for this field. "
                        "The values are preserved only in non-authoritative migration metadata."
                    ),
                    source_ref=tid,
                    field_path=f"topics[{ti}].{field}",
                )


def normalize_flow_steps(behavior: dict[str, Any]) -> None:
    for flow in behavior.get("flows", []) or []:
        for step in flow.get("steps", []) or []:
            step.setdefault("subflow_refs", [])
            step.setdefault("resume_ref", None)


def migrate_document(doc: dict[str, Any], path: Path) -> tuple[dict[str, Any], bool]:
    fmt = doc.get("format")
    if not isinstance(fmt, dict):
        return doc, False
    if fmt.get("format_version") != "1.4":
        return doc, False

    contract_id = doc.get("identity", {}).get("id", path.stem)
    old_structure = doc.pop("structure", {}) or {}
    old_topics = doc.pop("topics", []) or []

    links = list(doc.get("links", []) or [])
    gaps = list(doc.get("gaps", []) or [])
    members = list(doc.get("members", []) or [])

    convert_structure(old_structure, links)
    convert_topics(old_topics, members, links, gaps, contract_id)

    doc["format"] = {
        "contract_format": "CANONICAL_CONTRACT",
        "format_version": "1.6",
    }
    doc["status"] = "unlocked"
    doc.setdefault("maturity", "structured")
    doc["members"] = members
    doc["links"] = links
    doc["gaps"] = gaps

    behavior = doc.setdefault("behavior", {
        "states": [], "interfaces": [], "operations": [], "events": [], "flows": []
    })
    for k in ("states", "interfaces", "operations", "events", "flows"):
        behavior.setdefault(k, [])
    normalize_flow_steps(behavior)

    doc.setdefault("semantics", {})
    doc.setdefault("constraints", {"invariants": [], "hard_gates": []})
    doc["constraints"].setdefault("invariants", [])
    doc["constraints"].setdefault("hard_gates", [])
    doc.setdefault("references", [])
    doc.setdefault("prose", {"summary": "", "notes": []})
    doc["prose"].setdefault("summary", "")
    doc["prose"].setdefault("notes", [])

    metadata = doc.setdefault("metadata", {})
    metadata["migration"] = {
        "from_format_version": "1.4",
        "to_format_version": "1.6",
        "migration_kind": "serialization_and_progressive_formalization",
        "lock_state_after_migration": "unlocked",
        "legacy_structure_preserved_as_authority": False,
        "legacy_topics_preserved_as_authority": False,
        "note": "Only mappings explicitly defined by Canonical Contract Format 1.6 were promoted to typed Links.",
    }
    metadata["migration_legacy_non_authoritative"] = {
        "structure": old_structure,
        "topics": old_topics,
    }

    # A shared 1.6 boundary master requires architecture.root_ref. Existing
    # 1.4 AIGMos did not carry the final 1.6 architecture tree, so do not guess.
    if doc.get("source_role") == "boundary_master" and not doc.get("architecture", {}).get("root_ref"):
        append_gap(
            doc["gaps"],
            gap_id=f"MIG16_ARCH_ROOT_{contract_id}",
            gap_type="unresolved_architecture_root",
            description="CW 1.6 boundary_master requires architecture.root_ref; migration does not infer it from legacy roots or filesystem layout.",
            target_ref=contract_id,
            field_path="architecture.root_ref",
        )

    return doc, True


def main() -> int:
    changed = 0
    failed = 0
    for path in sorted(CANONICAL.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            new_doc, did_change = migrate_document(doc, path)
            if did_change:
                path.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                changed += 1
        except Exception as exc:
            print(f"ERROR {path.relative_to(ROOT)}: {exc}")
            failed += 1

    print(json.dumps({
        "migration": "AIGMos CW 1.4 -> 1.6",
        "changed_files": changed,
        "failed_files": failed,
        "result": "ok" if failed == 0 else "failed",
    }, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
