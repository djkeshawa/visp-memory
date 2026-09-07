"""Transactional Evidence and governed memory writes for the optional Neo4j backend."""

import json
import logging
import uuid
from contextlib import contextmanager

from visp_memory.core.belief_write import prepare_belief
from visp_memory.core.clock import utc_now_iso
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.storage import (
    EvidenceImmutableError,
    EvidenceReferenceError,
    LocalStorage,
    repository_memory_write,
)
from visp_memory.quality.secrets import SecretBearingContentError, redact_for_storage

logger = logging.getLogger(__name__)


def lock_graph(tx):
    """Serialize governed mutations across processes, including journal operations."""
    tx.run(
        "MERGE (l:StorageLock {id: 'governance'}) SET l.revision = coalesce(l.revision, 0) + 1"
    ).consume()


class Neo4jGovernance:
    @contextmanager
    def _write_session(self):
        with self.driver.session() as session, session.begin_transaction() as tx:
            lock_graph(tx)
            yield tx
            tx.commit()

    @staticmethod
    def _validate_governed_graph(tx):
        from visp_memory.core.beliefs import normalize_belief_type, normalize_epistemic_status
        from visp_memory.core.storage import StorageMigrationRequired

        try:
            for row in tx.run("MATCH (e:Evidence) RETURN e"):
                LocalStorage._evidence_row_to_dict(dict(row["e"]))
            for row in tx.run(
                "MATCH (m:Memory) OPTIONAL MATCH (m)-[:CITES]->(e:Evidence) "
                "RETURN m, collect(e.id) AS citations, collect(e.repo_id) AS repos"
            ):
                memory = row["m"]
                if not memory.get("repo_id"):
                    raise ValueError("Memory has no repository scope")
                ids = memory.get("evidence_ids", [])
                if set(ids) != set(row["citations"]):
                    raise ValueError("Memory citation edges do not match its Evidence IDs")
                if any(repo != memory["repo_id"] for repo in row["repos"]):
                    raise ValueError("Evidence citation crosses repository scope")
                if memory.get("layer") in {"raw", "episodic", "semantic"} and not ids:
                    raise ValueError("Governed memory is missing Evidence citations")
                if memory.get("layer") == "semantic":
                    kind = normalize_belief_type(memory.get("belief_type"))
                    normalize_epistemic_status(memory.get("epistemic_status"))
                    if memory.get("category") != kind:
                        raise ValueError("Semantic category differs from its belief type")
        except (ValueError, EvidenceReferenceError, EvidenceImmutableError) as exc:
            raise StorageMigrationRequired(f"Invalid Neo4j Evidence graph: {exc}") from exc

    def _ensure_governance_schema(self):
        with self.driver.session() as session:
            for label, field in (
                ("Evidence", "id"),
                ("StorageLock", "id"),
                ("AuthorityAttestation", "nonce_key"),
            ):
                session.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{field} IS UNIQUE"
                ).consume()
            session.run(
                "MATCH (v:SchemaVersion {component: 'storage'}) SET v.evidence_version = 1"
            ).consume()

    @staticmethod
    def _evidence(tx, evidence_id):
        row = tx.run("MATCH (e:Evidence {id: $id}) RETURN e", id=evidence_id).single()
        return LocalStorage._evidence_row_to_dict(dict(row["e"])) if row else None

    @staticmethod
    def _insert_graph_evidence(tx, record, compare_created_at=True):
        existing = Neo4jGovernance._evidence(tx, record["id"])
        if existing:
            expected = LocalStorage._evidence_row_to_dict(record)
            keys = set(expected) - ({"created_at"} if not compare_created_at else set())
            if any(existing.get(key) != expected[key] for key in keys):
                raise EvidenceImmutableError(f"Evidence {record['id']!r} is immutable")
        else:
            tx.run("CREATE (e:Evidence) SET e = $record", record=record).consume()

    def store_evidence(
        self,
        content,
        repo_id,
        evidence_type="observation",
        provenance="unknown",
        metadata=None,
        evidence_id=None,
        created_at=None,
    ):
        if not isinstance(content, str) or not content:
            raise ValueError("Evidence content must be a non-empty string")
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise EvidenceReferenceError("Evidence requires a non-empty repository ID")
        content = LocalStorage._redact_evidence_content(content)
        record = dict(
            id=evidence_id or f"ev-{uuid.uuid4().hex}",
            content=content,
            repo_id=repo_id,
            evidence_type=evidence_type,
            provenance="unknown" if repo_id == UNSCOPED_REPO_ID else provenance,
            metadata=json.dumps(metadata or {}),
            content_hash=LocalStorage._evidence_hash(content),
            created_at=created_at or utc_now_iso(),
        )

        def write(tx):
            lock_graph(tx)
            self._insert_graph_evidence(tx, record, created_at is not None)

        with self.driver.session() as session:
            session.execute_write(write)
        return record["id"]

    def get_evidence(self, evidence_id):
        with self.driver.session() as session:
            return session.execute_read(self._evidence, evidence_id)

    def list_evidence(self, repo_id, *, limit=10000):
        with self.driver.session() as session:
            return [
                LocalStorage._evidence_row_to_dict(dict(row["e"]))
                for row in session.run(
                    "MATCH (e:Evidence {repo_id: $repo}) RETURN e ORDER BY e.created_at, e.id "
                    "LIMIT $limit",
                    repo=repo_id,
                    limit=limit,
                )
            ]

    def update_evidence(self, evidence_id, **kwargs):
        raise EvidenceImmutableError(f"Evidence {evidence_id!r} is immutable")

    def get_authority_attestation(self, belief_id):
        with self.driver.session() as session:
            row = session.run(
                "MATCH (a:AuthorityAttestation {belief_id: $id}) RETURN a", id=belief_id
            ).single()
            return {k: v for k, v in dict(row["a"]).items() if k != "nonce_key"} if row else None

    def _resolve_evidence(self, tx, memory_id, layer, repo_id, source_ids, evidence_ids):
        if memory_id in evidence_ids:
            raise EvidenceReferenceError("A belief cannot cite itself as Evidence")
        resolved = list(evidence_ids)
        for source_id in dict.fromkeys(source_ids):
            row = tx.run("MATCH (m:Memory {id: $id}) RETURN m", id=source_id).single()
            if not row or row["m"].get("status") == "deleted":
                raise EvidenceReferenceError(f"Lineage source {source_id!r} is missing or deleted")
            if row["m"].get("repo_id") != repo_id:
                raise EvidenceReferenceError("Lineage sources must belong to the same repository")
            resolved.extend(row["m"].get("evidence_ids", []))
        resolved = list(dict.fromkeys(resolved))
        if layer == "semantic" and not resolved:
            raise EvidenceReferenceError("A semantic belief requires at least one evidence record")
        for evidence_id in resolved:
            evidence = self._evidence(tx, evidence_id)
            if not evidence or evidence["repo_id"] != repo_id:
                raise EvidenceReferenceError("Evidence must exist in the same repository")
        return resolved

    @staticmethod
    def _link_evidence(tx, memory_id, evidence_ids):
        tx.run(
            "MATCH (m:Memory {id: $id}) SET m.evidence_ids = $evidence_ids "
            "WITH m UNWIND $evidence_ids AS eid MATCH (e:Evidence {id: eid}) "
            "MERGE (m)-[:CITES]->(e)",
            id=memory_id,
            evidence_ids=evidence_ids,
        ).consume()

    def attach_evidence(self, belief_id, evidence_ids, *, repo_id):
        def write(tx):
            lock_graph(tx)
            row = tx.run("MATCH (m:Memory {id: $id}) RETURN m", id=belief_id).single()
            if not row or row["m"].get("layer") != "semantic" or row["m"].get("repo_id") != repo_id:
                raise EvidenceReferenceError("Evidence can attach only to a same-repository belief")
            resolved = self._resolve_evidence(tx, belief_id, "semantic", repo_id, [], evidence_ids)
            self._link_evidence(
                tx, belief_id, list(dict.fromkeys([*row["m"].get("evidence_ids", []), *resolved]))
            )

        with self.driver.session() as session:
            session.execute_write(write)

    @repository_memory_write
    def store_memory(
        self,
        content,
        layer="episodic",
        repo_id=None,
        category=None,
        importance=0.5,
        tags=None,
        metadata=None,
        source_ids=None,
        evidence_ids=None,
        status="active",
        source=None,
        quality_flags=None,
        embedding=None,
        auto_link=True,
        auto_link_limit=3,
        auto_link_min_score=0.53,
        epistemic_status=None,
        authority_attestation=None,
        replaces_belief_id=None,
        memory_id=None,
        created_at=None,
    ):
        if layer not in {"raw", "episodic", "semantic", "intent"}:
            raise ValueError("Memory layer must be one of: episodic, intent, raw, semantic")
        try:
            content, quality_flags = redact_for_storage(
                content, quality_flags, reject_if_redacted=authority_attestation is not None
            )
        except SecretBearingContentError as exc:
            from visp_memory.core.authority import ProhibitionAuthorityError

            raise ProhibitionAuthorityError(str(exc)) from exc
        memory_id = memory_id or self._generate_id(content)
        created_at = created_at or utc_now_iso()
        repo_id = repo_id or UNSCOPED_REPO_ID
        category, belief_type, epistemic_status, metadata = prepare_belief(
            layer,
            category,
            epistemic_status,
            metadata or {},
            created_at,
            authority_attestation,
            replaces_belief_id,
        )
        if repo_id == UNSCOPED_REPO_ID:
            from visp_memory.core.trust import Provenance, with_provenance

            tags, source = with_provenance(tags or [], Provenance.UNKNOWN), "unknown"
        if embedding is None and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(content)
            except Exception as exc:
                logger.warning("Embedding computation failed: %s", exc)
        record = dict(
            id=memory_id,
            content=content,
            layer=layer,
            repo_id=repo_id,
            category=category,
            belief_type=belief_type,
            epistemic_status=epistemic_status,
            importance=importance,
            tags=tags or [],
            metadata=json.dumps(metadata),
            source_ids=source_ids or [],
            status=status,
            source=source,
            quality_flags=quality_flags or [],
            created_at=created_at,
            accessed_at=created_at,
            access_count=0,
        )
        if embedding:
            record[self._vector_property] = embedding
        captured_id = f"ev-{uuid.uuid4().hex}"

        def write(tx):
            lock_graph(tx)
            ids = list(evidence_ids or [])
            if layer in {"raw", "episodic"} and not ids:
                self._insert_graph_evidence(
                    tx,
                    dict(
                        id=captured_id,
                        content=content,
                        repo_id=repo_id,
                        evidence_type="raw" if layer == "raw" else "observation",
                        provenance=source or "unknown",
                        created_at=created_at,
                        content_hash=LocalStorage._evidence_hash(content),
                        metadata=json.dumps({"captured_memory_id": memory_id, "exact_input": True}),
                    ),
                )
                ids = [captured_id]
            resolved = self._resolve_evidence(tx, memory_id, layer, repo_id, source_ids or [], ids)
            attestation = self._verify_graph_authority(
                tx, record, resolved, authority_attestation, replaces_belief_id
            )
            if attestation is False:  # Identical signed retry.
                return
            if tx.run("MATCH (m:Memory {id: $id}) RETURN m", id=memory_id).single():
                raise ValueError(f"Memory {memory_id!r} already exists")
            tx.run(
                f"CREATE (m:Memory:{layer.capitalize()}) SET m = $record, "
                "m.created_at = datetime($created), m.accessed_at = datetime($created)",
                record=record,
                created=created_at,
            ).consume()
            self._link_evidence(tx, memory_id, resolved)
            if attestation:
                tx.run(
                    "CREATE (a:AuthorityAttestation) SET a = $attestation "
                    "WITH a MATCH (m:Memory {id: $id}) SET m.authority_attestation = $envelope",
                    id=memory_id,
                    attestation=attestation,
                    envelope=json.dumps({k: v for k, v in attestation.items() if k != "nonce_key"}),
                ).consume()
            tx.run(
                "MERGE (r:Repository {id: $repo}) ON CREATE SET r.name = $repo, "
                "r.status = 'active', r.metadata = '{}', r.tech_stack = [], "
                "r.created_at = $created",
                repo=repo_id,
                created=created_at,
            ).consume()
            for source_id in dict.fromkeys(source_ids or []):
                tx.run(
                    "MATCH (a:Memory {id: $source}), (b:Memory {id: $target}) "
                    "MERGE (a)-[r:DERIVED_FROM]->(b) SET r.id = $id, r.weight = 1.0, "
                    "r.confidence = 'observed', r.source = 'lineage', "
                    "r.reason = 'Explicit source memory', r.created_at = $created",
                    source=source_id,
                    target=memory_id,
                    id=f"{source_id}-{memory_id}",
                    created=created_at,
                ).consume()

        with self.driver.session() as session:
            session.execute_write(write)
        # Similarity links are optional enrichment; source provenance above is atomic.
        self._auto_link_memory(
            memory_id,
            content,
            repo_id,
            [],
            enabled=auto_link,
            limit=auto_link_limit,
            min_score=auto_link_min_score,
        )
        return memory_id

    def _verify_graph_authority(self, tx, record, evidence_ids, envelope, replaces_belief_id):
        if record["belief_type"] != "prohibition":
            return None
        from visp_memory.core.authority import (
            ProhibitionAuthorityError,
            verify_prohibition_attestation,
        )

        if envelope is None:
            raise ProhibitionAuthorityError("prohibition belief requires an authority attestation")
        evidence = [self._evidence(tx, eid) for eid in sorted(evidence_ids)]
        verified = verify_prohibition_attestation(
            envelope,
            content=record["content"],
            repo_id=record["repo_id"],
            metadata=json.loads(record["metadata"]),
            evidence=[{"id": e["id"], "content_hash": e["content_hash"]} for e in evidence],
            expected_replaces_belief_id=replaces_belief_id,
        )
        nonce_key = json.dumps([verified.key_id, verified.nonce])
        old = tx.run(
            "MATCH (a:AuthorityAttestation {nonce_key: $key}) RETURN a", key=nonce_key
        ).single()
        if old:
            row = tx.run("MATCH (m:Memory {id: $id}) RETURN m", id=record["id"]).single()
            fields = (
                "content",
                "layer",
                "repo_id",
                "belief_type",
                "epistemic_status",
                "metadata",
                "status",
            )
            if (
                old["a"]["digest"] != verified.digest
                or old["a"]["belief_id"] != record["id"]
                or not row
                or any(row["m"].get(k) != record[k] for k in fields)
                or row["m"].get("evidence_ids") != evidence_ids
            ):
                raise ProhibitionAuthorityError("prohibition attestation replay conflicts")
            return False
        return dict(
            digest=verified.digest,
            belief_id=record["id"],
            key_id=verified.key_id,
            nonce=verified.nonce,
            envelope=verified.envelope,
            created_at=record["created_at"],
            nonce_key=nonce_key,
        )
