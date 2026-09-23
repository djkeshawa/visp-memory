"""Explicit offline graph backup, empty-store restore, and legacy Neo4j migration.

Run with ``python -m visp_memory.core.neo4j_admin --help``. Stop application writers first.
Credentials are read from the normal NEO4J_* configuration, never command-line arguments.
"""

import argparse
import json
import os
import re
from datetime import timedelta
from pathlib import Path

from visp_memory.core.clock import parse_utc, utc_now_iso
from visp_memory.core.neo4j_governance import lock_graph
from visp_memory.core.storage import (
    STORAGE_SCHEMA_VERSION,
    EvidenceReferenceError,
    LocalStorage,
    StorageMigrationRequired,
)

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _encode(value):
    if hasattr(value, "iso_format"):
        return {"$neo4j_datetime": value.iso_format()}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"Unsupported backup property type: {type(value).__name__}")


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"$neo4j_datetime"}:
            from neo4j.time import DateTime

            return DateTime.from_iso_format(value["$neo4j_datetime"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _snapshot(tx):
    nodes = [
        dict(key=row["key"], labels=row["labels"], properties=_encode(dict(row["n"])))
        for row in tx.run(
            "MATCH (n) WHERE NOT n:StorageLock RETURN elementId(n) AS key, labels(n) AS labels, n"
        )
    ]
    relationships = [
        dict(
            source=row["source"],
            target=row["target"],
            type=row["type"],
            properties=_encode(dict(row["r"])),
        )
        for row in tx.run(
            "MATCH (a)-[r]->(b) WHERE NOT a:StorageLock "
            "AND NOT b:StorageLock RETURN elementId(a) AS source, "
            "elementId(b) AS target, type(r) AS type, r"
        )
    ]
    return dict(
        format="visp-neo4j-backup-1",
        created_at=utc_now_iso(),
        nodes=nodes,
        relationships=relationships,
    )


def _save(snapshot, path):
    # Refuse overwriting backups and keep memory contents private to the operator.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(snapshot, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def backup_graph(driver, path):
    with driver.session() as session, session.begin_transaction() as tx:
        lock_graph(tx)
        snapshot = _snapshot(tx)
        _save(snapshot, path)
        tx.commit()
    return {"nodes": len(snapshot["nodes"]), "relationships": len(snapshot["relationships"])}


def restore_graph(driver, path):
    snapshot = json.loads(Path(path).read_text())
    if snapshot.get("format") != "visp-neo4j-backup-1":
        raise ValueError("Unsupported Neo4j backup format")
    keys = {n["key"] for n in snapshot["nodes"]}
    if len(keys) != len(snapshot["nodes"]):
        raise ValueError("Duplicate backup node keys")
    for node in snapshot["nodes"]:
        if not node["labels"] or any(not IDENTIFIER.fullmatch(x) for x in node["labels"]):
            raise ValueError("Invalid backup node label")
    for rel in snapshot["relationships"]:
        if not IDENTIFIER.fullmatch(rel["type"]) or {rel["source"], rel["target"]} - keys:
            raise ValueError("Invalid backup relationship")
    with driver.session() as session, session.begin_transaction() as tx:
        lock_graph(tx)
        if tx.run("MATCH (n) WHERE NOT n:StorageLock RETURN n LIMIT 1").single():
            raise ValueError("Restore requires an empty database; existing data is never replaced")
        mapped = {}
        for node in snapshot["nodes"]:
            labels = ":".join(node["labels"])
            row = tx.run(
                f"CREATE (n:{labels}) SET n = $properties RETURN elementId(n) AS key",
                properties=_decode(node["properties"]),
            ).single()
            mapped[node["key"]] = row["key"]
        for rel in snapshot["relationships"]:
            tx.run(
                f"MATCH (a), (b) WHERE elementId(a) = $source AND elementId(b) = $target "
                f"CREATE (a)-[r:{rel['type']}]->(b) SET r = $properties",
                source=mapped[rel["source"]],
                target=mapped[rel["target"]],
                properties=_decode(rel["properties"]),
            ).consume()
        tx.commit()
    return {"nodes": len(keys), "relationships": len(snapshot["relationships"])}


def migrate_graph(driver, backup_path):
    """Preserve legacy IDs/history; label captured legacy bytes as unverified snapshots."""
    from visp_memory.core.beliefs import migrate_legacy_belief_fields
    from visp_memory.core.eligibility import UNSCOPED_REPO_ID
    from visp_memory.core.neo4j_governance import Neo4jGovernance
    from visp_memory.core.trust import Provenance, with_provenance

    with driver.session() as session, session.begin_transaction() as tx:
        lock_graph(tx)
        markers = list(tx.run("MATCH (v:SchemaVersion {component: 'storage'}) RETURN v"))
        if len(markers) != 1 or markers[0]["v"].get("version") not in {4, 5}:
            raise StorageMigrationRequired("Only marked Neo4j schema 4 or 5 can be migrated")
        if markers[0]["v"].get("evidence_version") == 1:
            raise ValueError("This graph already uses governed Evidence storage")
        _save(_snapshot(tx), backup_path)
        rows = list(tx.run("MATCH (m:Memory) RETURN m"))
        by_id = {row["m"]["id"]: dict(row["m"]) for row in rows}
        if len(by_id) != len(rows):
            raise ValueError("Legacy memory IDs are not unique")
        for memory_id, memory in by_id.items():
            layer = memory.get("layer")
            if layer not in {"raw", "episodic", "semantic", "intent"}:
                raise ValueError("Unsupported legacy memory layer")
            repo = memory.get("repo_id") or UNSCOPED_REPO_ID
            sources = LocalStorage._json_deserialize(memory.get("source_ids")) or []
            for source in sources:
                if (
                    source not in by_id
                    or (by_id[source].get("repo_id") or UNSCOPED_REPO_ID) != repo
                ):
                    raise EvidenceReferenceError(
                        "Legacy lineage is dangling or crosses repositories"
                    )
            content = LocalStorage._redact_evidence_content(memory["content"])
            created = str(memory.get("created_at") or utc_now_iso())
            eid = "ev-neo4j-legacy-" + memory_id
            Neo4jGovernance._insert_graph_evidence(
                tx,
                dict(
                    id=eid,
                    content=content,
                    repo_id=repo,
                    content_hash=LocalStorage._evidence_hash(content),
                    evidence_type="legacy_snapshot",
                    provenance="unknown",
                    created_at=created,
                    metadata=json.dumps({"legacy_memory_id": memory_id, "verified": False}),
                ),
            )
            updates = dict(
                content=content,
                repo_id=repo,
                source_ids=sources,
                tags=with_provenance(
                    LocalStorage._json_deserialize(memory.get("tags")) or [], Provenance.UNKNOWN
                ),
                source="unknown",
            )
            if layer == "semantic":
                category, epistemic = migrate_legacy_belief_fields(
                    memory.get("category"), memory.get("status")
                )
                updates.update(category=category, belief_type=category, epistemic_status=epistemic)
                if category == "hypothesis":
                    metadata = LocalStorage._json_deserialize(memory.get("metadata")) or {}
                    start = parse_utc(created)
                    if start is None:
                        raise ValueError("Legacy hypothesis has invalid creation time")
                    metadata["valid_to"] = (start + timedelta(days=7)).isoformat()
                    updates["metadata"] = json.dumps(metadata)
            tx.run(
                "MATCH (m:Memory {id: $id}) SET m += $updates", id=memory_id, updates=updates
            ).consume()
            Neo4jGovernance._link_evidence(tx, memory_id, [eid])
            tx.run(
                "MERGE (r:Repository {id: $repo}) ON CREATE SET r.name = $repo, "
                "r.status = 'active', r.metadata = '{}', r.created_at = $created",
                repo=repo,
                created=created,
            ).consume()
        tx.run(
            "MATCH (v:SchemaVersion {component: 'storage'}) "
            "SET v.version = $version, v.evidence_version = 1",
            version=STORAGE_SCHEMA_VERSION,
        ).consume()
        tx.commit()
    return {"migrated_memories": len(rows), "backup": str(backup_path)}


def main():
    from neo4j import GraphDatabase

    from visp_memory.config import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("backup", "restore", "migrate"):
        command = sub.add_parser(name)
        command.add_argument("path", type=Path, help="Backup path (must be new for backup/migrate)")
    args = parser.parse_args()
    config = load_config().storage
    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password)
    ) as driver:
        operation = {"backup": backup_graph, "restore": restore_graph, "migrate": migrate_graph}
        print(json.dumps(operation[args.action](driver, args.path)))


if __name__ == "__main__":
    main()
