#!/usr/bin/env python3
"""Migrate legacy AIGMos Topic member trace surfaces by declared semantic kind.

Rules:
- Topic member -> Contract Entity: topic_member Link.
- Topic member -> migrated State/Interface/structural Link/flow-symbol Property: topic_member Link.
- Topic member -> invariant/hard gate: AIGMos DependencyRules Rule coverage, NOT CCF Link.
- Unknown/ambiguous: explicit Gap; never guess.

The source of truth for classification is the owning legacy contract first. This is
required because legacy local IDs such as GATE_1 repeat across contracts.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical" / "json"
DR_PATH = ROOT / "AIGMos_Dependency_Rules_v2.0.0-migration.json"
REPORT = ROOT / "cw20-topic-trace-migration-report.json"


def sid(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "UNKNOWN"))


def qid(owner: str, kind: str, local: Any) -> str:
    return f"{sid(owner)}::{kind}::{sid(local)}"


def legacy(d: dict[str, Any]) -> dict[str, Any]:
    return ((d.get("metadata", {}) or {}).get("migration_legacy_non_authoritative", {}) or {})


def property_obj(pid: str, ptype: str, ruleset: str, value: Any, metadata=None) -> dict[str, Any]:
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


def topic_member_link(owner: str, tid: str, index: int, target_ref: str, source_path: str) -> dict[str, Any]:
    return property_obj(
        qid(owner, "LINK", f"TOPIC_MEMBER_{tid}_{index}"),
        "link",
        "RULESET_LINK_TOPIC_MEMBER",
        {
            "link_type_ref": "topic_member",
            "parent_ref": tid,
            "child_ref": target_ref,
            "properties": {},
        },
        {"migration_source_path": source_path, "migration_semantic_resolution": "declared_owner_scope"},
    )


def load_docs():
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(CANONICAL.rglob("*.json"))]


def global_contract_index(docs):
    out = defaultdict(set)
    for _, d in docs:
        cid = (d.get("identity", {}) or {}).get("id")
        if cid:
            out[cid].add(cid)
    return out


def local_index(d: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    owner = (d.get("identity", {}) or {}).get("id", "UNKNOWN")
    old = legacy(d)
    idx = defaultdict(list)

    constraints = d.get("constraints", {}) or {}
    for inv in constraints.get("invariants", []) or []:
        if inv.get("id"):
            idx[inv["id"]].append({
                "kind": "invariant",
                "target_ref": qid(owner, "RULE::INVARIANT", inv["id"]),
                "source": inv,
            })
    for gate in constraints.get("hard_gates", []) or []:
        if gate.get("id"):
            idx[gate["id"]].append({
                "kind": "hard_gate",
                "target_ref": qid(owner, "RULE::HARD_GATE", gate["id"]),
                "source": gate,
            })

    for member in old.get("members", []) or []:
        if isinstance(member, dict) and member.get("id"):
            mtype = member.get("type") or "unspecified"
            idx[member["id"]].append({
                "kind": f"legacy_member:{mtype}",
                "target_ref": qid(owner, "FLOW_SYMBOL", member["id"]),
                "source": member,
            })

    behavior = old.get("behavior", {}) or {}
    for field, kind, qkind in [
        ("states", "state", "STATES"),
        ("interfaces", "interface", "INTERFACES"),
        ("operations", "operation", "OPERATIONS"),
        ("events", "event", "EVENT"),
        ("flows", "flow", "FLOWS"),
    ]:
        for obj in behavior.get(field, []) or []:
            if isinstance(obj, dict) and obj.get("id"):
                idx[obj["id"]].append({"kind": kind, "target_ref": qid(owner, qkind, obj["id"]), "source": obj})

    structure = old.get("structure", {}) or {}
    for field in ("containment", "relations", "ownership", "authority", "dependencies"):
        for edge in structure.get(field, []) or []:
            if isinstance(edge, dict) and edge.get("id"):
                idx[edge["id"]].append({
                    "kind": f"structural_link:{field}",
                    "target_ref": qid(owner, "LINK", edge["id"]),
                    "source": edge,
                })
    return idx


def resolve(ref: str, local, global_contracts):
    c = local.get(ref, [])
    if len(c) == 1:
        return c[0]
    if len(c) > 1:
        return {"kind": "ambiguous", "candidates": c}
    g = global_contracts.get(ref, set())
    if len(g) == 1:
        return {"kind": "contract", "target_ref": ref, "source": None}
    if len(g) > 1:
        return {"kind": "ambiguous", "candidates": sorted(g)}
    return {"kind": "unresolved"}


def main() -> int:
    docs = load_docs()
    global_contracts = global_contract_index(docs)
    dr = json.loads(DR_PATH.read_text(encoding="utf-8"))

    dr_rules: dict[str, dict[str, Any]] = {r["id"]: r for r in dr.get("rules", []) or [] if r.get("id")}
    dr_ruleset = dr.setdefault("ruleset", {
        "id": "AIGMOS_CW_2_0_RULESET",
        "name": "AIGMos CW 2.0 Ruleset",
        "status": "unlocked",
        "parent_ruleset_ref": "CW_CCF_2_0_RULESET",
        "direct_rule_refs": [],
    })
    direct = set(dr_ruleset.get("direct_rule_refs", []) or [])

    counts = Counter()
    unresolved = []
    ambiguous = []
    affected = 0

    for p, d in docs:
        old = legacy(d)
        topics = old.get("topics", []) or []
        if not topics:
            continue
        owner = (d.get("identity", {}) or {}).get("id", p.stem)
        entities = d.get("entities", []) or []
        owner_entity = next((e for e in entities if e.get("id") == owner), None)
        if owner_entity is None:
            raise SystemExit(f"Owner Entity missing: {p}: {owner}")
        props = owner_entity.setdefault("properties", [])

        # Remove the old mechanical member_ref -> Link result. Other Topic links remain untouched.
        props[:] = [
            prop for prop in props
            if not (
                prop.get("property_type_ref") == "link"
                and str((prop.get("metadata", {}) or {}).get("migration_source_path", "")).startswith("topics[")
                and ".member_refs[" in str((prop.get("metadata", {}) or {}).get("migration_source_path", ""))
            )
        ]

        # Remove cleanup gaps/evidence created only because mechanical Topic-member Links targeted non-CCF identities.
        d["gaps"] = [
            g for g in (d.get("gaps", []) or [])
            if not (
                g.get("type") == "unresolved_link_endpoint"
                and "::LINK::TOPIC_MEMBER_" in str(g.get("property_ref", ""))
            )
        ]
        meta = d.setdefault("metadata", {})
        ev = meta.get("migration_unresolved_links", []) or []
        ev = [x for x in ev if "::LINK::TOPIC_MEMBER_" not in str(((x.get("property") or {}).get("id", "")))]
        if ev:
            meta["migration_unresolved_links"] = ev
        else:
            meta.pop("migration_unresolved_links", None)

        local = local_index(d)
        existing_prop_ids = {x.get("id") for e in entities for x in (e.get("properties", []) or [])}
        changed = False

        for ti, topic in enumerate(topics):
            tid = topic.get("id")
            if not tid:
                continue
            for ri, raw in enumerate(topic.get("member_refs", []) or []):
                ref = (raw.get("id") or raw.get("ref")) if isinstance(raw, dict) else raw
                if not ref:
                    continue
                resolved = resolve(str(ref), local, global_contracts)
                kind = resolved["kind"]
                source_path = f"topics[{ti}].member_refs[{ri}]"

                if kind in {"invariant", "hard_gate"}:
                    rule_id = resolved["target_ref"]
                    source = resolved["source"] or {}
                    rule = dr_rules.setdefault(rule_id, {
                        "id": rule_id,
                        "name": source.get("name", source.get("id", rule_id)),
                        "kind": kind,
                        "status": "unlocked",
                        "owner_contract_ref": owner,
                        "source_local_ref": ref,
                        "definition": {k: v for k, v in source.items() if k != "id"},
                        "topic_refs": [],
                        "migration": {"source": source_path, "semantic_guessing": False},
                    })
                    if tid not in rule["topic_refs"]:
                        rule["topic_refs"].append(tid)
                    direct.add(rule_id)
                    counts[f"rule:{kind}"] += 1
                    changed = True
                    continue

                if kind.startswith("legacy_member:flow_"):
                    target = resolved["target_ref"]
                    if target not in existing_prop_ids:
                        source = resolved["source"] or {}
                        props.append(property_obj(
                            target,
                            "aigmos_flow_symbol",
                            "AIGMOS_RULESET_FLOW_SYMBOL",
                            {
                                "symbol_type_ref": source.get("type"),
                                "name": source.get("name"),
                                "semantics": source.get("semantics", {}),
                            },
                            {"migration_source_path": "members[]", "legacy_member_ref": ref},
                        ))
                        existing_prop_ids.add(target)
                    props.append(topic_member_link(owner, tid, ri, target, source_path))
                    counts["link:flow_symbol"] += 1
                    changed = True
                    continue

                if kind in {"contract", "state", "interface"} or kind.startswith("structural_link:"):
                    target = resolved["target_ref"]
                    if target not in existing_prop_ids and kind != "contract":
                        # The target was declared but its migrated Property was not created; do not invent it here.
                        unresolved.append({"file": str(p.relative_to(ROOT)), "topic": tid, "ref": ref, "kind": kind, "expected_target": target})
                        continue
                    props.append(topic_member_link(owner, tid, ri, target, source_path))
                    counts[f"link:{kind}"] += 1
                    changed = True
                    continue

                if kind in {"operation", "event", "flow"}:
                    target = resolved["target_ref"]
                    if target not in existing_prop_ids:
                        unresolved.append({"file": str(p.relative_to(ROOT)), "topic": tid, "ref": ref, "kind": kind, "expected_target": target})
                        continue
                    props.append(topic_member_link(owner, tid, ri, target, source_path))
                    counts[f"link:{kind}"] += 1
                    changed = True
                    continue

                record = {"file": str(p.relative_to(ROOT)), "topic": tid, "ref": ref, "kind": kind, "source_path": source_path}
                if kind == "ambiguous":
                    record["candidates"] = resolved.get("candidates", [])
                    ambiguous.append(record)
                else:
                    unresolved.append(record)

        if unresolved or ambiguous:
            # Add only this document's newly unresolved records as explicit gaps below.
            pass

        if changed:
            affected += 1
            p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Add unresolved Topic-member migration findings as explicit migration-level gaps in DR.
    dr["rules"] = sorted(dr_rules.values(), key=lambda x: x["id"])
    dr_ruleset["direct_rule_refs"] = sorted(direct)
    prs = dr.setdefault("property_rulesets", [])
    if not any(x.get("id") == "AIGMOS_RULESET_FLOW_SYMBOL" for x in prs):
        prs.append({
            "id": "AIGMOS_RULESET_FLOW_SYMBOL",
            "applies_to_property_type_ref": "aigmos_flow_symbol",
            "status": "unlocked",
            "value_shape": {"required_fields": ["symbol_type_ref", "semantics"]},
            "constraints": [
                "Flow symbol identity remains owner-scoped as declared by legacy semantics.",
                "No semantics are inferred from source literal, spelling, path or filename.",
                "Flow symbols are descriptive/causal model data and do not grant runtime authority."
            ]
        })
    dr["topic_trace_migration"] = {
        "rule": "Legacy Topic member trace coverage is split by declared semantic kind: Entity/Property membership is Link; invariant/hard-gate coverage is DependencyRules metadata.",
        "semantic_guessing": False,
        "unresolved": unresolved,
        "ambiguous": ambiguous,
    }
    DR_PATH.write_text(json.dumps(dr, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report = {
        "operation": "Legacy Topic trace-surface semantic migration",
        "affected_files": affected,
        "counts": dict(counts.most_common()),
        "dependency_rules_created": len(dr_rules),
        "unresolved_count": len(unresolved),
        "ambiguous_count": len(ambiguous),
        "unresolved_examples": unresolved[:500],
        "ambiguous_examples": ambiguous[:500],
        "semantic_guessing": False,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if unresolved or ambiguous:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
