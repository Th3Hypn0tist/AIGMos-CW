#!/usr/bin/env python3
"""Deterministic AIGMos canonical migration to locked CCF/CW-DR 2.0.

The migration is intentionally conservative:
- it never invents architecture or domain meaning;
- every migrated contract is unlocked;
- every legacy contract identity becomes an Entity;
- explicit directed structural edges become Link Properties;
- legacy Events become Event Properties;
- directed Event references are split into Link Properties when present;
- legacy semantics/operations/flows/interfaces/states are preserved as unlocked
  AIGMos Properties governed by an explicit AIGMos migration DR;
- unknown mappings remain explicit Gaps or non-authoritative migration evidence.

The locked standards are fetched separately by the workflow from aigm.fi.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"
TARGET_VERSION = "2.0"

LINK_RULESETS = {
    "containment": "RULESET_LINK_CONTAINMENT",
    "relation": "RULESET_LINK_RELATION",
    "ownership": "RULESET_LINK_OWNERSHIP",
    "authority": "RULESET_LINK_AUTHORITY",
    "dependency": "RULESET_LINK_DEPENDENCY",
    "topic_parent": "RULESET_LINK_TOPIC_PARENT",
    "topic_member": "RULESET_LINK_TOPIC_MEMBER",
    "topic_composition": "RULESET_LINK_TOPIC_COMPOSITION",
    "architecture_parent": "RULESET_LINK_ARCHITECTURE_PARENT",
    "surface": "RULESET_LINK_SURFACE",
    "implementation_binding": "RULESET_LINK_IMPLEMENTATION_BINDING",
    "event_read": "RULESET_LINK_EVENT_READ",
    "event_input": "RULESET_LINK_EVENT_INPUT",
    "event_output": "RULESET_LINK_EVENT_OUTPUT",
    "event_effect": "RULESET_LINK_EVENT_EFFECT",
    "event_cause": "RULESET_LINK_EVENT_CAUSE",
    "event_condition": "RULESET_LINK_EVENT_CONDITION",
    "effect_target": "RULESET_LINK_EFFECT_TARGET",
}


def sid(value: Any) -> str:
    s = str(value or "UNKNOWN")
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", s)


def qid(owner: str, kind: str, local: Any) -> str:
    return f"{sid(owner)}::{kind}::{sid(local)}"


def property_obj(pid: str, ptype: str, ruleset: str, value: Any,
                 *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "id": pid,
        "property_type_ref": ptype,
        "ruleset_ref": ruleset,
        "status": "unlocked",
        "value": value,
    }
    if metadata:
        out["metadata"] = metadata
    return out


def link_property(owner: str, local_id: Any, link_type: str,
                  parent_ref: str, child_ref: str,
                  properties: dict[str, Any] | None = None,
                  *, source_path: str | None = None) -> dict[str, Any]:
    value = {
        "link_type_ref": link_type,
        "parent_ref": parent_ref,
        "child_ref": child_ref,
        "properties": properties or {},
    }
    metadata = {"migration_source_path": source_path} if source_path else None
    return property_obj(
        qid(owner, "LINK", local_id),
        "link",
        LINK_RULESETS[link_type],
        value,
        metadata=metadata,
    )


def gap(gaps: list[dict[str, Any]], gid: str, gtype: str,
        description: str, *, entity_ref: str | None = None,
        field_path: str | None = None, severity: str = "blocking") -> None:
    g: dict[str, Any] = {
        "gap_id": gid,
        "type": gtype,
        "status": "open",
        "severity": severity,
        "description": description,
    }
    if entity_ref:
        g["entity_ref"] = entity_ref
    if field_path:
        g["field_path"] = field_path
    gaps.append(g)


def migrate_structure(owner: str, structure: dict[str, Any], props: list[dict[str, Any]]) -> None:
    for i, e in enumerate(structure.get("containment", []) or []):
        props.append(link_property(
            owner, e.get("id", i), "containment",
            e["parent_ref"], e["child_ref"],
            {k: v for k, v in e.items() if k not in {"id", "parent_ref", "child_ref"}},
            source_path=f"structure.containment[{i}]",
        ))
    for i, e in enumerate(structure.get("relations", []) or []):
        props.append(link_property(
            owner, e.get("id", i), "relation",
            e["source_ref"], e["target_ref"],
            {k: v for k, v in e.items() if k not in {"id", "source_ref", "target_ref", "direction"}},
            source_path=f"structure.relations[{i}]",
        ))
    for i, e in enumerate(structure.get("ownership", []) or []):
        props.append(link_property(
            owner, e.get("id", i), "ownership",
            e["owner_ref"], e["target_ref"],
            {k: v for k, v in e.items() if k not in {"id", "owner_ref", "target_ref"}},
            source_path=f"structure.ownership[{i}]",
        ))
    for i, e in enumerate(structure.get("authority", []) or []):
        props.append(link_property(
            owner, e.get("id", i), "authority",
            e["authority_ref"], e["target_ref"],
            {k: v for k, v in e.items() if k not in {"id", "authority_ref", "target_ref"}},
            source_path=f"structure.authority[{i}]",
        ))
    for i, e in enumerate(structure.get("dependencies", []) or []):
        # CCF 2.0 dependency role: parent_ref=dependency, child_ref=dependent.
        # Legacy 1.4 source depended on target, therefore target becomes parent.
        props.append(link_property(
            owner, e.get("id", i), "dependency",
            e["target_ref"], e["source_ref"],
            {k: v for k, v in e.items() if k not in {"id", "source_ref", "target_ref"}},
            source_path=f"structure.dependencies[{i}]",
        ))


def migrate_topics(owner: str, topics: list[dict[str, Any]],
                   entities: list[dict[str, Any]], props: list[dict[str, Any]],
                   gaps: list[dict[str, Any]]) -> None:
    for ti, topic in enumerate(topics or []):
        tid = topic.get("id")
        if not tid:
            gap(gaps, qid(owner, "GAP", f"TOPIC_ID_{ti}"), "missing_topic_identity",
                "Legacy Topic has no stable id; Topic Entity was not invented.",
                entity_ref=owner, field_path=f"topics[{ti}]")
            continue
        # Topic ids were globally referenced in 1.4; preserve the id.
        entities.append({
            "id": tid,
            "name": topic.get("name", tid),
            "entity_type_ref": "topic",
            "status": "unlocked",
            "properties": [
                property_obj(qid(tid, "PROPERTY", "SEMANTICS"), "aigmos_semantics",
                             "AIGMOS_RULESET_SEMANTICS",
                             {"purpose": topic.get("purpose", ""), "metadata": topic.get("metadata", {})})
            ],
        })
        for i, parent in enumerate(topic.get("parent_topic_refs", []) or []):
            props.append(link_property(owner, f"TOPIC_PARENT_{tid}_{i}", "topic_parent",
                                       parent, tid, {}, source_path=f"topics[{ti}].parent_topic_refs[{i}]"))
        for i, member in enumerate(topic.get("member_refs", []) or []):
            props.append(link_property(owner, f"TOPIC_MEMBER_{tid}_{i}", "topic_member",
                                       tid, member, {}, source_path=f"topics[{ti}].member_refs[{i}]"))
        for i, component in enumerate(topic.get("composed_topic_refs", []) or []):
            props.append(link_property(owner, f"TOPIC_COMPOSE_{tid}_{i}", "topic_composition",
                                       tid, component, {}, source_path=f"topics[{ti}].composed_topic_refs[{i}]"))
        for field in ("relation_refs", "operation_refs", "event_refs", "flow_refs", "child_topics"):
            if topic.get(field):
                gap(gaps, qid(owner, "GAP", f"TOPIC_{field}_{tid}"),
                    "legacy_topic_reference_mapping_unresolved",
                    f"Legacy Topic field {field} is preserved in migration evidence; CCF 2.0 does not justify inventing a Link specialization for it.",
                    entity_ref=tid, field_path=f"topics[{ti}].{field}", severity="required")


def migrate_behavior(owner: str, behavior: dict[str, Any], props: list[dict[str, Any]]) -> None:
    for kind, ptype, ruleset in (
        ("states", "aigmos_state", "AIGMOS_RULESET_STATE"),
        ("interfaces", "aigmos_interface", "AIGMOS_RULESET_INTERFACE"),
        ("operations", "aigmos_operation", "AIGMOS_RULESET_OPERATION"),
        ("flows", "aigmos_flow", "AIGMOS_RULESET_FLOW"),
    ):
        for i, item in enumerate(behavior.get(kind, []) or []):
            local = item.get("id", i) if isinstance(item, dict) else i
            props.append(property_obj(qid(owner, kind.upper(), local), ptype, ruleset, item,
                                      metadata={"migration_source_path": f"behavior.{kind}[{i}]"}))

    for i, event in enumerate(behavior.get("events", []) or []):
        if not isinstance(event, dict):
            event = {"legacy_value": event}
        eid = qid(owner, "EVENT", event.get("id", i))
        embedded = {}
        # Event itself remains lean. Directed refs are split below.
        for key, value in event.items():
            if key not in {"reads", "inputs", "outputs", "effects", "causes", "conditions"}:
                embedded[key] = value
        props.append(property_obj(
            eid, "event", "RULESET_EVENT",
            {"event_type_ref": event.get("type_ref", event.get("id", "legacy_event")), "properties": embedded},
            metadata={"migration_source_path": f"behavior.events[{i}]"},
        ))
        for field, ltype, reverse in (
            ("reads", "event_read", False),
            ("inputs", "event_input", False),
            ("outputs", "event_output", True),
            ("causes", "event_cause", False),
            ("conditions", "event_condition", False),
        ):
            for j, ref in enumerate(event.get(field, []) or []):
                # Only explicit scalar refs are promoted. Complex legacy payloads remain inside migration evidence.
                if isinstance(ref, str):
                    parent, child = (eid, ref) if reverse else (ref, eid)
                    props.append(link_property(owner, f"EVENT_{field}_{i}_{j}", ltype, parent, child,
                                               {}, source_path=f"behavior.events[{i}].{field}[{j}]"))
        for j, effect in enumerate(event.get("effects", []) or []):
            effect_value = effect if isinstance(effect, dict) else {"legacy_value": effect}
            effect_id = qid(owner, "EFFECT", f"{event.get('id', i)}_{j}")
            props.append(property_obj(effect_id, "effect", "RULESET_EFFECT",
                                      {"effect_type_ref": effect_value.get("effect_type_ref", "legacy_effect"),
                                       "properties": effect_value},
                                      metadata={"migration_source_path": f"behavior.events[{i}].effects[{j}]"}))
            props.append(link_property(owner, f"EVENT_EFFECT_{i}_{j}", "event_effect", eid, effect_id,
                                       {}, source_path=f"behavior.events[{i}].effects[{j}]"))
            target = effect_value.get("target_ref")
            if isinstance(target, str):
                props.append(link_property(owner, f"EFFECT_TARGET_{i}_{j}", "effect_target", effect_id, target,
                                           {}, source_path=f"behavior.events[{i}].effects[{j}].target_ref"))


def migrate_document(doc: dict[str, Any], path: Path) -> tuple[dict[str, Any], bool]:
    fmt = doc.get("format")
    if not isinstance(fmt, dict):
        return doc, False
    source_version = str(fmt.get("format_version", ""))
    if source_version not in {"1.4", "1.6", "1.7"}:
        return doc, False

    ident = doc.get("identity") or {}
    cid = ident.get("id", path.stem)
    props: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    gaps = list(doc.get("gaps", []) or [])

    structure = doc.get("structure", {}) or {}
    if structure:
        migrate_structure(cid, structure, props)

    # Support intermediate 1.6/1.7 migration trees too.
    for i, old in enumerate(doc.get("links", []) or []):
        ltype = old.get("type_ref")
        if ltype not in LINK_RULESETS:
            gap(gaps, qid(cid, "GAP", f"LINK_TYPE_{i}"), "link_type_unresolved",
                f"Legacy Link type {ltype!r} has no explicit CW 2.0 migration Ruleset mapping.",
                entity_ref=cid, field_path=f"links[{i}]")
            continue
        if "parent_ref" in old and "child_ref" in old:
            parent, child = old["parent_ref"], old["child_ref"]
        else:
            src, dst = old.get("source_ref"), old.get("target_ref")
            if ltype in {"architecture_parent", "dependency", "topic_parent"}:
                parent, child = dst, src
            else:
                parent, child = src, dst
        if parent and child:
            props.append(link_property(cid, old.get("id", i), ltype, parent, child,
                                       old.get("properties", {}), source_path=f"links[{i}]"))

    migrate_behavior(cid, doc.get("behavior", {}) or {}, props)

    semantics = doc.get("semantics", {}) or {}
    if semantics:
        props.append(property_obj(qid(cid, "PROPERTY", "SEMANTICS"), "aigmos_semantics",
                                  "AIGMOS_RULESET_SEMANTICS", semantics,
                                  metadata={"migration_source_path": "semantics"}))

    entity = {
        "id": cid,
        "name": ident.get("name", cid),
        "entity_type_ref": ident.get("type", "canonical_contract"),
        "status": "unlocked",
        "properties": props,
        "metadata": {
            "legacy_identity_version": ident.get("version"),
            "legacy_source_role": doc.get("source_role"),
            "migration_source_file": str(path.relative_to(ROOT)),
        },
    }
    entities.append(entity)
    migrate_topics(cid, doc.get("topics", []) or [], entities, props, gaps)

    # Keep model-level CCF mechanisms where they remain valid.
    out = {
        "format": {"contract_format": "CANONICAL_CONTRACT", "format_version": TARGET_VERSION},
        "identity": ident,
        "status": "unlocked",
        "purpose": doc.get("purpose", ""),
        "scope": doc.get("scope", {"owns": [], "does_not_own": []}),
        "entities": entities,
        "constraints": doc.get("constraints", {"invariants": [], "hard_gates": []}),
        "references": doc.get("references", []),
        "gaps": gaps,
        "prose": doc.get("prose", {"summary": "", "notes": []}),
        "metadata": dict(doc.get("metadata", {}) or {}),
    }
    out["metadata"]["migration"] = {
        "from_format_version": source_version,
        "to_format_version": TARGET_VERSION,
        "status_after_migration": "unlocked",
        "semantic_guessing": False,
        "legacy_parallel_authority_preserved": False,
    }
    out["metadata"]["migration_legacy_non_authoritative"] = {
        "members": doc.get("members", []),
        "structure": structure,
        "topics": doc.get("topics", []),
        "behavior": doc.get("behavior", {}),
        "semantics": semantics,
    }
    return out, True


def write_aigmos_dr() -> None:
    dr = {
        "name": "AIGMos Dependency Rules",
        "type": "dependency_rules",
        "version": "2.0.0-migration",
        "status": "unlocked",
        "inheritance": {
            "model": "DependencyRules",
            "parent_ruleset_ref": "CW_CCF_2_0_RULESET",
            "base_semantics": "inherited_unchanged",
        },
        "purpose": "Provide explicit unlocked Rulesets for legacy AIGMos semantic categories during deterministic CCF 2.0 migration.",
        "property_rulesets": [
            {"id": "AIGMOS_RULESET_SEMANTICS", "applies_to_property_type_ref": "aigmos_semantics", "status": "unlocked", "value_shape": {"required_fields": []}, "constraints": []},
            {"id": "AIGMOS_RULESET_STATE", "applies_to_property_type_ref": "aigmos_state", "status": "unlocked", "value_shape": {"required_fields": []}, "constraints": []},
            {"id": "AIGMOS_RULESET_INTERFACE", "applies_to_property_type_ref": "aigmos_interface", "status": "unlocked", "value_shape": {"required_fields": []}, "constraints": []},
            {"id": "AIGMOS_RULESET_OPERATION", "applies_to_property_type_ref": "aigmos_operation", "status": "unlocked", "value_shape": {"required_fields": []}, "constraints": []},
            {"id": "AIGMOS_RULESET_FLOW", "applies_to_property_type_ref": "aigmos_flow", "status": "unlocked", "value_shape": {"required_fields": []}, "constraints": []}
        ],
        "rule": "These migration Rulesets preserve explicit legacy data without claiming domain semantics that have not yet been reviewed under CW 2.0."
    }
    (ROOT / "AIGMos_Dependency_Rules_v2.0.0-migration.json").write_text(
        json.dumps(dr, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    changed = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    for path in sorted(CANONICAL.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            new_doc, did_change = migrate_document(doc, path)
            if did_change:
                path.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                changed += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    write_aigmos_dr()
    report = {
        "migration": "AIGMos CW -> CCF/CW-DR 2.0",
        "changed_files": changed,
        "skipped_files": skipped,
        "failed_files": failed,
        "result": "ok" if failed == 0 else "failed",
        "errors": errors,
    }
    (ROOT / "cw20-migration-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
