"""ArcadeDB storage backend."""

import hashlib
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from visp_memory.core.authority import (
    ProhibitionAuthorityError,
    verify_prohibition_attestation,
)
from visp_memory.core.beliefs import (
    HYPOTHESIS_TTL_DAYS,
    EpistemicStatus,
    normalize_belief_type,
    normalize_epistemic_status,
)
from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.ranking import rank_memory_results, text_similarity, utility_rank_adjustment
from visp_memory.core.storage import (
    REINFORCING_RECALL_EVENTS,
    STORAGE_SCHEMA_VERSION,
    BaseStorage,
    EvidenceError,
    EvidenceImmutableError,
    EvidenceReferenceError,
    LocalStorage,
    MemoryLayer,
    MemoryStatus,
    SessionCompletionStatus,
    StorageCapabilities,
    StorageMigrationRequired,
)
from visp_memory.quality.secrets import SecretBearingContentError, redact_for_storage

logger = logging.getLogger(__name__)

ARCADEDB_INSTALL_MESSAGE = (
    "ArcadeDB storage requires the optional ArcadeDB extra. "
    'Install it with: pip install "visp-memory[arcadedb]"'
)


class ArcadeDbDependencyError(ImportError):
    """Raised when ArcadeDB storage is selected without the optional extra."""


def load_arcadedb_driver():
    """Load the optional ArcadeDB embedded driver."""
    try:
        import arcadedb_embedded as arcadedb
    except ImportError as exc:
        raise ArcadeDbDependencyError(ARCADEDB_INSTALL_MESSAGE) from exc
    return arcadedb


class ArcadeDbStorage(BaseStorage):
    """ArcadeDB-backed storage."""

    MEMORY_TYPE = "Memory"
    SESSION_TYPE = "Session"
    VERTEX_TYPES = [
        "Memory",
        "Evidence",
        "Intent",
        "Session",
        "Repository",
        "User",
        "Team",
        "AuditLog",
        "RecallFeedback",
        "SchemaVersion",
        "AuthorityAttestation",
    ]
    EDGE_TYPES = [
        "MemoryRelationship",
        "BeliefEvidence",
        "BeliefAuthority",
        "RepoDependency",
        "TeamMember",
    ]
    MEMORY_RELATIONSHIP_EDGE = "MemoryRelationship"
    REPO_DEPENDENCY_EDGE = "RepoDependency"
    TEAM_MEMBER_EDGE = "TeamMember"
    BELIEF_EVIDENCE_EDGE = "BeliefEvidence"
    BELIEF_AUTHORITY_EDGE = "BeliefAuthority"
    MEMORY_JSON_FIELDS = {"tags", "metadata", "source_ids", "quality_flags"}
    MEMORY_FIELDS = [
        "id",
        "content",
        "layer",
        "repo_id",
        "category",
        "belief_type",
        "epistemic_status",
        "importance",
        "tags",
        "metadata",
        "source_ids",
        "status",
        "source",
        "quality_flags",
        "created_at",
        "accessed_at",
        "access_count",
        "approved_by",
        "approved_at",
        "archived_at",
    ]
    EVIDENCE_FIELDS = [
        "id",
        "content",
        "content_hash",
        "repo_id",
        "evidence_type",
        "provenance",
        "metadata",
        "created_at",
    ]
    EVIDENCE_JSON_FIELDS = {"metadata"}
    BELIEF_EVIDENCE_FIELDS = ["id", "belief_id", "evidence_id", "created_at"]
    AUTHORITY_ATTESTATION_FIELDS = [
        "id",
        "belief_id",
        "key_id",
        "nonce",
        "digest",
        "envelope",
        "created_at",
    ]
    BELIEF_AUTHORITY_FIELDS = [
        "id",
        "belief_id",
        "attestation_id",
        "created_at",
    ]
    RELATIONSHIP_FIELDS = [
        "id",
        "source_id",
        "target_id",
        "relationship",
        "strength",
        "confidence",
        "confidence_score",
        "source",
        "source_file",
        "source_location",
        "reason",
        "created_by",
        "created_at",
    ]
    INTENT_FIELDS = [
        "id",
        "description",
        "priority",
        "context",
        "repo_id",
        "status",
        "created_at",
        "updated_at",
    ]
    REPOSITORY_FIELDS = [
        "id",
        "name",
        "url",
        "description",
        "tech_stack",
        "team_id",
        "metadata",
        "status",
        "archived_at",
        "created_at",
    ]
    USER_FIELDS = ["id", "username", "email", "display_name", "metadata"]
    TEAM_FIELDS = ["id", "name", "description", "metadata"]
    AUDIT_FIELDS = [
        "id",
        "event_type",
        "actor_id",
        "repo_id",
        "target_type",
        "target_id",
        "metadata",
        "created_at",
    ]
    RECALL_EVENT_FIELDS = [
        "id",
        "memory_id",
        "event_type",
        "repo_id",
        "query_hash",
        "task_id",
        "outcome",
        "metadata",
        "created_at",
    ]
    REPO_DEPENDENCY_FIELDS = [
        "id",
        "source_repo_id",
        "target_repo_id",
        "type",
        "version",
        "notes",
    ]
    TEAM_MEMBER_FIELDS = ["id", "team_id", "user_id"]
    SCHEMA_VERSION_FIELDS = ["id", "component", "version", "applied_at"]
    RECORD_JSON_FIELDS = {
        "Intent": {"context"},
        "Repository": {"tech_stack", "metadata"},
        "User": {"metadata"},
        "Team": {"metadata"},
        "AuditLog": {"metadata"},
        "RecallFeedback": {"metadata"},
    }
    RECORD_FIELDS = {
        "Intent": INTENT_FIELDS,
        "Repository": REPOSITORY_FIELDS,
        "User": USER_FIELDS,
        "Team": TEAM_FIELDS,
        "AuditLog": AUDIT_FIELDS,
        "RecallFeedback": RECALL_EVENT_FIELDS,
        "SchemaVersion": SCHEMA_VERSION_FIELDS,
        "AuthorityAttestation": AUTHORITY_ATTESTATION_FIELDS,
    }

    def __init__(
        self,
        data_dir: Path,
        embedding_fn=None,
        embedding_dimension: int | None = None,
    ):
        self._arcadedb = load_arcadedb_driver()
        self.data_dir = Path(data_dir) / "arcadedb"
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_fn = embedding_fn
        self._upgrade_session_schema_marker = False
        self._embedding_dimension = embedding_dimension
        self._intent_outcome_lock = Lock()
        # ArcadeDB search is lexical (keyword) only — embeddings are not indexed.
        # Surface that explicitly so an operator who configured a real vector
        # provider knows vector recall is unavailable on this backend.
        embedding_owner = getattr(embedding_fn, "__self__", None)
        owner_name = embedding_owner.__class__.__name__.lower() if embedding_owner else ""
        if embedding_fn is not None and owner_name != "noopprovider":
            logger.warning(
                "ArcadeDB backend performs lexical (keyword) search only; the configured "
                "embedding provider will not be used for vector recall."
            )
        self._init_schema()

    def _database(self):
        database_path = str(self.data_dir)
        if self._arcadedb.database_exists(database_path):
            return self._arcadedb.open_database(database_path)
        return self._arcadedb.create_database(database_path)

    def _init_schema(self) -> None:
        database_existed = self._arcadedb.database_exists(str(self.data_dir))
        with self._database() as db:
            marker_exists = False
            if database_existed:
                marker_exists = self._probe_schema_compatibility(db)
            with db.transaction():
                for vertex_type in self.VERTEX_TYPES:
                    db.command("sql", f"CREATE VERTEX TYPE {vertex_type} IF NOT EXISTS")
                for edge_type in self.EDGE_TYPES:
                    db.command("sql", f"CREATE EDGE TYPE {edge_type} IF NOT EXISTS")
                if not marker_exists:
                    db.command(
                        "sql",
                        "INSERT INTO SchemaVersion SET id = ?, component = ?, version = ?, "
                        "applied_at = ?",
                        "storage",
                        "storage",
                        STORAGE_SCHEMA_VERSION,
                        utc_now().isoformat(),
                    )
                elif self._upgrade_session_schema_marker:
                    db.command(
                        "sql",
                        "UPDATE SchemaVersion SET version = ?, applied_at = ? WHERE id = ?",
                        STORAGE_SCHEMA_VERSION,
                        utc_now().isoformat(),
                        "storage",
                    )

    def _probe_schema_compatibility(self, db) -> bool:
        """Read existing schema state before any type or marker mutation."""
        try:
            schema_types = self._rows(
                db.query("sql", "SELECT name, type, records FROM schema:types")
            )
        except Exception as exc:
            raise StorageMigrationRequired(
                "ArcadeDB schema compatibility could not be proven without mutation"
            ) from exc

        type_counts: Dict[str, int] = {}
        type_kinds: Dict[str, str] = {}
        for item in schema_types:
            name = self._record_get(item, "name")
            kind = self._record_get(item, "type")
            records = self._record_get(item, "records")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(kind, str)
                or kind.upper() not in {"VERTEX", "EDGE"}
                or not isinstance(records, int)
                or records < 0
                or name in type_counts
            ):
                raise StorageMigrationRequired(
                    "ArcadeDB schema metadata is malformed; compatibility is unknown"
                )
            type_counts[name] = records
            type_kinds[name] = kind.upper()

        versions: List[Any] = []
        if "SchemaVersion" in type_counts:
            try:
                versions = self._rows(
                    db.query(
                        "sql", "SELECT FROM SchemaVersion WHERE id = ?", "storage"
                    )
                )
            except Exception as exc:
                raise StorageMigrationRequired(
                    "ArcadeDB schema marker could not be read safely"
                ) from exc
        if versions:
            if len(versions) != 1:
                raise StorageMigrationRequired("ArcadeDB storage schema marker is ambiguous")
            try:
                stored_version = int(
                    self._record_get(versions[0], "version", 0) or 0
                )
            except (TypeError, ValueError) as exc:
                raise StorageMigrationRequired(
                    "ArcadeDB storage schema marker is malformed"
                ) from exc
            if stored_version > STORAGE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Storage schema is newer than this visp-memory build "
                    f"({stored_version} > {STORAGE_SCHEMA_VERSION})"
                )
            if stored_version < STORAGE_SCHEMA_VERSION - 1:
                raise StorageMigrationRequired(
                    "ArcadeDB schema migration is not implemented; export the older "
                    f"store with its original build before using schema v{STORAGE_SCHEMA_VERSION}"
                )
            self._validate_current_v4_graph(db, type_kinds, type_counts)
            if stored_version == STORAGE_SCHEMA_VERSION - 1:
                self._upgrade_session_schema_marker = True
            return True
        if any(type_counts.values()):
            raise StorageMigrationRequired(
                "Unversioned non-empty ArcadeDB storage requires an explicit migration"
            )
        return False

    def _validate_current_v4_graph(
        self,
        db,
        type_kinds: Dict[str, str],
        type_counts: Dict[str, int],
    ) -> None:
        """Prove the current governed graph without mutating or repairing it."""
        required_kinds = {
            **{name: "VERTEX" for name in self.VERTEX_TYPES},
            **{name: "EDGE" for name in self.EDGE_TYPES},
        }
        missing = sorted(required_kinds.keys() - type_kinds.keys())
        wrong_kind = sorted(
            name
            for name, expected in required_kinds.items()
            if type_kinds.get(name) not in {None, expected}
        )
        if missing or wrong_kind:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if wrong_kind:
                details.append("wrong kind " + ", ".join(wrong_kind))
            raise StorageMigrationRequired(
                "ArcadeDB schema v4 required types are invalid: " + "; ".join(details)
            )

        try:
            memories = self._rows(db.query("sql", "SELECT FROM Memory"))
            evidence = self._rows(db.query("sql", "SELECT FROM Evidence"))
            links = self._rows(db.query("sql", "SELECT FROM BeliefEvidence"))
            attestations = self._rows(
                db.query("sql", "SELECT FROM AuthorityAttestation")
            )
            authority_links = self._rows(db.query("sql", "SELECT FROM BeliefAuthority"))
        except Exception as exc:
            raise StorageMigrationRequired(
                "ArcadeDB schema-v4 governed graph could not be read safely"
            ) from exc

        rows_by_kind = {
            "Memory": memories,
            "Evidence": evidence,
            "BeliefEvidence": links,
            "AuthorityAttestation": attestations,
            "BeliefAuthority": authority_links,
        }
        for kind, rows in rows_by_kind.items():
            if len(rows) != type_counts[kind]:
                raise StorageMigrationRequired(
                    f"ArcadeDB schema-v4 governed graph has an ambiguous {kind} count"
                )

        def indexed(rows: List[Any], kind: str) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for row in rows:
                row_id = self._record_get(row, "id")
                if not isinstance(row_id, str) or not row_id.strip() or row_id in result:
                    raise StorageMigrationRequired(
                        f"ArcadeDB schema-v4 governed graph has ambiguous {kind} IDs"
                    )
                result[row_id] = row
            return result

        memories_by_id = indexed(memories, "Memory")
        evidence_by_id = indexed(evidence, "Evidence")
        links_by_id = indexed(links, "BeliefEvidence")
        attestations_by_id = indexed(attestations, "AuthorityAttestation")
        authority_links_by_id = indexed(authority_links, "BeliefAuthority")
        if memories_by_id.keys() & evidence_by_id.keys():
            raise StorageMigrationRequired(
                "ArcadeDB schema-v3 Evidence graph has cross-type ID collisions"
            )

        for kind, records in (
            ("Memory", memories_by_id),
            ("Evidence", evidence_by_id),
        ):
            for record_id, record in records.items():
                repo_id = self._record_get(record, "repo_id")
                if not isinstance(repo_id, str) or not repo_id.strip():
                    raise StorageMigrationRequired(
                        "ArcadeDB schema-v3 Evidence graph has an unscoped "
                        f"{kind} record {record_id!r}"
                    )

        for evidence_id, record in evidence_by_id.items():
            missing_fields = [
                field
                for field in self.EVIDENCE_FIELDS
                if self._record_get(record, field) is None
            ]
            if missing_fields:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v3 Evidence graph record "
                    f"{evidence_id!r} is missing fields: "
                    + ", ".join(sorted(missing_fields))
                )
            content = self._record_get(record, "content")
            content_hash = self._record_get(record, "content_hash")
            if (
                not isinstance(content, str)
                or not content
                or not isinstance(content_hash, str)
                or LocalStorage._evidence_hash(content) != content_hash
            ):
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v3 Evidence graph record "
                    f"{evidence_id!r} has an invalid content hash"
                )
            if LocalStorage._redact_evidence_content(content) != content:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v3 Evidence graph contains secret-bearing "
                    f"Evidence {evidence_id!r}"
                )

        links_by_belief: Dict[str, int] = {}
        for link_id, link in links_by_id.items():
            belief_id = self._record_get(link, "belief_id")
            evidence_id = self._record_get(link, "evidence_id")
            if (
                not isinstance(belief_id, str)
                or not belief_id
                or not isinstance(evidence_id, str)
                or not evidence_id
            ):
                raise StorageMigrationRequired(
                    f"ArcadeDB schema-v3 Evidence graph link {link_id!r} is malformed"
                )
            belief = memories_by_id.get(belief_id)
            source = evidence_by_id.get(evidence_id)
            if belief is None or source is None:
                raise StorageMigrationRequired(
                    f"ArcadeDB schema-v3 Evidence graph link {link_id!r} is dangling"
                )
            if self._record_get(belief, "repo_id") != self._record_get(
                source, "repo_id"
            ):
                raise StorageMigrationRequired(
                    f"ArcadeDB schema-v3 Evidence graph link {link_id!r} crosses repositories"
                )
            links_by_belief[belief_id] = links_by_belief.get(belief_id, 0) + 1

        for memory_id, memory in memories_by_id.items():
            if (
                self._record_get(memory, "layer") == "semantic"
                and links_by_belief.get(memory_id, 0) < 1
            ):
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 Evidence graph contains a semantic Memory "
                    f"without Evidence: {memory_id!r}"
                )

        for memory_id, memory in memories_by_id.items():
            layer = self._record_get(memory, "layer")
            category = self._record_get(memory, "category")
            belief_type = self._record_get(memory, "belief_type")
            epistemic_status = self._record_get(memory, "epistemic_status")
            if layer == "semantic":
                try:
                    normalized_type = normalize_belief_type(belief_type)
                    normalized_status = normalize_epistemic_status(epistemic_status)
                except ValueError as exc:
                    raise StorageMigrationRequired(
                        "ArcadeDB schema-v4 governed Memory fields are invalid: "
                        f"{memory_id!r}"
                    ) from exc
                if category != normalized_type:
                    raise StorageMigrationRequired(
                        "ArcadeDB schema-v4 governed Memory category/type mismatch: "
                        f"{memory_id!r}"
                    )
                if normalized_type == "hypothesis" and normalized_status != "hypothesized":
                    raise StorageMigrationRequired(
                        "ArcadeDB schema-v4 governed hypothesis status is invalid"
                    )
                if normalized_type == "prohibition" and normalized_status != "observed":
                    raise StorageMigrationRequired(
                        "ArcadeDB schema-v4 governed prohibition status is invalid"
                    )
            elif belief_type is not None or epistemic_status is not None:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 governed fields appear on a non-semantic Memory"
                )

        authority_by_belief: Dict[str, int] = {}
        nonce_digests: Dict[tuple[str, str], str] = {}
        for _attestation_id, attestation in attestations_by_id.items():
            missing_fields = [
                field
                for field in self.AUTHORITY_ATTESTATION_FIELDS
                if self._record_get(attestation, field) is None
            ]
            if missing_fields:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 AuthorityAttestation is missing fields"
                )
            belief_id = self._record_get(attestation, "belief_id")
            key_id = self._record_get(attestation, "key_id")
            nonce = self._record_get(attestation, "nonce")
            digest = self._record_get(attestation, "digest")
            envelope = self._record_get(attestation, "envelope")
            if (
                belief_id not in memories_by_id
                or not isinstance(key_id, str)
                or not key_id
                or not isinstance(nonce, str)
                or not nonce
                or not isinstance(envelope, str)
                or hashlib.sha256(envelope.encode("utf-8")).hexdigest() != digest
            ):
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 AuthorityAttestation is malformed"
                )
            nonce_key = (key_id, nonce)
            if nonce_key in nonce_digests:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 AuthorityAttestation nonce is duplicated"
                )
            nonce_digests[nonce_key] = digest

        for link_id, link in authority_links_by_id.items():
            belief_id = self._record_get(link, "belief_id")
            attestation_id = self._record_get(link, "attestation_id")
            attestation = attestations_by_id.get(attestation_id)
            if (
                belief_id not in memories_by_id
                or attestation is None
                or self._record_get(attestation, "belief_id") != belief_id
            ):
                raise StorageMigrationRequired(
                    f"ArcadeDB schema-v4 BeliefAuthority link {link_id!r} is malformed"
                )
            authority_by_belief[belief_id] = authority_by_belief.get(belief_id, 0) + 1

        for memory_id, memory in memories_by_id.items():
            expected = 1 if self._record_get(memory, "belief_type") == "prohibition" else 0
            if authority_by_belief.get(memory_id, 0) != expected:
                raise StorageMigrationRequired(
                    "ArcadeDB schema-v4 prohibition authority graph is incomplete"
                )

    def get_capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            vector_search=False,
            audit_log=True,
            reindex=False,
            complete_graph_export=True,
        )

    def get_schema_status(self) -> Dict[str, Any]:
        with self._database() as db:
            rows = self._rows(
                db.query("sql", "SELECT FROM SchemaVersion WHERE id = ?", "storage")
            )
        stored_version = int(self._record_get(rows[0], "version", 0) or 0) if rows else 0
        return {
            "current_version": STORAGE_SCHEMA_VERSION,
            "stored_version": stored_version,
            "status": "ready" if stored_version == STORAGE_SCHEMA_VERSION else "migration_required",
        }

    @staticmethod
    def _json_serialize(data: Any) -> str:
        return LocalStorage._json_serialize(data)

    @staticmethod
    def _json_deserialize(data: str) -> Any:
        return LocalStorage._json_deserialize(data)

    @staticmethod
    def _generate_id(content: str) -> str:
        return LocalStorage._generate_id(content)

    @staticmethod
    def _rows(result) -> List[Any]:
        if result is None:
            return []
        return list(result)

    @staticmethod
    def _record_get(record, field: str, default=None):
        if isinstance(record, dict):
            return record.get(field, default)
        getter = getattr(record, "get", None)
        if getter is not None:
            try:
                return getter(field)
            except TypeError:
                return getter(field, default)
        return getattr(record, field, default)

    @classmethod
    def _memory_record_to_dict(cls, record) -> Dict[str, Any]:
        memory = {
            field: cls._record_get(record, field)
            for field in cls.MEMORY_FIELDS
            if cls._record_get(record, field) is not None
        }
        for field in cls.MEMORY_JSON_FIELDS:
            if field in memory:
                parsed = cls._json_deserialize(memory[field])
                if parsed is None:
                    parsed = {} if field == "metadata" else []
                memory[field] = parsed
        memory.setdefault("tags", [])
        memory.setdefault("metadata", {})
        memory.setdefault("source_ids", [])
        memory.setdefault("quality_flags", [])
        memory.setdefault("access_count", 0)
        return memory

    @classmethod
    def _evidence_record_to_dict(cls, record) -> Dict[str, Any]:
        evidence = cls._record_to_dict(
            record, cls.EVIDENCE_FIELDS, cls.EVIDENCE_JSON_FIELDS
        )
        content = evidence.get("content")
        content_hash = evidence.get("content_hash")
        if not isinstance(content, str) or not content:
            raise EvidenceError("Refusing to read malformed Evidence content")
        LocalStorage._require_secret_free_evidence(
            content, context="reading secret-bearing Evidence"
        )
        if LocalStorage._evidence_hash(content) != content_hash:
            raise EvidenceError("Refusing to read Evidence with an invalid content hash")
        evidence.setdefault("metadata", {})
        evidence["record_type"] = "evidence"
        return evidence

    def _belief_evidence_ids(self, db, belief_id: str) -> List[str]:
        rows = self._rows(db.query("sql", f"SELECT FROM {self.BELIEF_EVIDENCE_EDGE}"))
        return sorted(
            {
                self._record_get(row, "evidence_id")
                for row in rows
                if self._record_get(row, "belief_id") == belief_id
                and self._record_get(row, "evidence_id")
            }
        )

    def _authority_attestation_for_belief(self, db, belief_id: str) -> Optional[str]:
        rows = self._rows(db.query("sql", "SELECT FROM AuthorityAttestation"))
        matches = [
            row for row in rows if self._record_get(row, "belief_id") == belief_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ProhibitionAuthorityError(
                "stored prohibition has ambiguous authority attestations"
            )
        return self._record_get(matches[0], "envelope")

    def _validate_belief_references(
        self,
        db,
        *,
        memory_id: str,
        layer: str,
        repo_id: str,
        source_ids: List[str],
        evidence_ids: List[str],
    ) -> List[str]:
        if memory_id in evidence_ids:
            raise EvidenceReferenceError("A belief cannot cite itself as Evidence")

        resolved = list(dict.fromkeys(evidence_ids))
        for source_id in dict.fromkeys(source_ids):
            source_rows = self._rows(
                db.query("sql", f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?", source_id)
            )
            if not source_rows or self._record_get(source_rows[0], "status") == "deleted":
                raise EvidenceReferenceError(
                    f"Lineage source {source_id!r} is missing or deleted"
                )
            if self._record_get(source_rows[0], "repo_id") != repo_id:
                raise EvidenceReferenceError(
                    "Lineage sources must belong to the same repository"
                )
            resolved.extend(self._belief_evidence_ids(db, source_id))

        resolved = list(dict.fromkeys(resolved))
        if layer == "semantic" and not resolved:
            raise EvidenceReferenceError(
                "A semantic belief requires at least one evidence record"
            )
        for evidence_id in resolved:
            rows = self._rows(
                db.query("sql", "SELECT FROM Evidence WHERE id = ?", evidence_id)
            )
            if not rows:
                raise EvidenceReferenceError(
                    f"Evidence {evidence_id!r} is missing or is not an Evidence record"
                )
            if self._record_get(rows[0], "repo_id") != repo_id:
                raise EvidenceReferenceError("Evidence must belong to the same repository")
        return resolved

    @classmethod
    def _record_to_dict(
        cls, record, fields: List[str], json_fields: set[str] = None
    ) -> Dict[str, Any]:
        json_fields = json_fields or set()
        item = {
            field: cls._record_get(record, field)
            for field in fields
            if cls._record_get(record, field) is not None
        }
        for field in json_fields:
            if field in item:
                parsed = cls._json_deserialize(item[field])
                if parsed is None:
                    parsed = [] if field == "tech_stack" else {}
                item[field] = parsed
        return item

    @classmethod
    def _relationship_record_to_dict(cls, record) -> Dict[str, Any]:
        relationship = {
            field: cls._record_get(record, field)
            for field in cls.RELATIONSHIP_FIELDS
            if cls._record_get(record, field) is not None
        }
        evidence_data = {
            "confidence": relationship.pop("confidence", None),
            "confidence_score": relationship.pop("confidence_score", None),
            "source": relationship.pop("source", None),
            "source_file": relationship.pop("source_file", None),
            "source_location": relationship.pop("source_location", None),
            "reason": relationship.pop("reason", None),
            "created_by": relationship.pop("created_by", None),
            "created_at": relationship.get("created_at"),
        }
        relationship["evidence"] = LocalStorage._normalize_relationship_evidence(
            evidence_data,
            strength=relationship.get("strength"),
            created_at=relationship.get("created_at"),
            legacy=evidence_data["source"] == "legacy",
        )
        return relationship

    def _query_memory(self, memory_id: str):
        with self._database() as db:
            rows = self._rows(
                db.query("sql", f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?", memory_id)
            )
            if not rows:
                return None
            memory = self._memory_record_to_dict(rows[0])
            memory["evidence_ids"] = self._belief_evidence_ids(db, memory_id)
            authority_attestation = self._authority_attestation_for_belief(db, memory_id)
            if authority_attestation is not None:
                memory["authority_attestation"] = authority_attestation
            return memory

    def _query_memories(
        self,
        *,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at DESC",
        exclude_raw: bool = False,
    ) -> List[Dict[str, Any]]:
        query = f"SELECT FROM {self.MEMORY_TYPE}"
        conditions = []
        params: list[Any] = []

        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        elif exclude_raw:
            # Exclude the 'raw' layer in-query so raw rows never consume candidate
            # slots before the importance cap (NULL layer is preserved).
            conditions.append("(layer IS NULL OR layer <> 'raw')")
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        allowed_order_by = {
            "created_at DESC",
            "created_at ASC",
            "importance DESC",
            "importance ASC",
            "accessed_at DESC",
            "accessed_at ASC",
        }
        if order_by not in allowed_order_by:
            order_by = "created_at DESC"

        query += f" ORDER BY {order_by}, id {'ASC' if order_by.endswith('ASC') else 'DESC'}"
        query += " SKIP ? LIMIT ?"
        params.extend((max(0, offset), limit))

        with self._database() as db:
            rows = self._rows(db.query("sql", query, *params))
            memories = [self._memory_record_to_dict(row) for row in rows]
            for memory in memories:
                memory["evidence_ids"] = self._belief_evidence_ids(db, memory["id"])
                authority_attestation = self._authority_attestation_for_belief(
                    db, memory["id"]
                )
                if authority_attestation is not None:
                    memory["authority_attestation"] = authority_attestation
            return memories

    def store_evidence(
        self,
        content: str,
        repo_id: str,
        evidence_type: str = "observation",
        provenance: str = "unknown",
        metadata: Dict[str, Any] = None,
        evidence_id: str = None,
        created_at: str = None,
    ) -> str:
        if not isinstance(content, str) or not content:
            raise ValueError("Evidence content must be a non-empty string")
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise EvidenceReferenceError("Evidence requires a non-empty repository ID")
        content = LocalStorage._redact_evidence_content(content)
        if repo_id == UNSCOPED_REPO_ID:
            provenance = "unknown"
        evidence_id = evidence_id or f"ev-{uuid.uuid4().hex}"
        compare_created_at = created_at is not None
        created_at = created_at or utc_now().isoformat()
        record = {
            "id": evidence_id,
            "content": content,
            "content_hash": LocalStorage._evidence_hash(content),
            "repo_id": repo_id,
            "evidence_type": evidence_type,
            "provenance": provenance,
            "metadata": metadata or {},
            "created_at": created_at,
        }
        with self._database() as db:
            with db.transaction():
                rows = self._rows(
                    db.query("sql", "SELECT FROM Evidence WHERE id = ?", evidence_id)
                )
                if rows:
                    existing = self._evidence_record_to_dict(rows[0])
                    comparable_fields = set(record) - {"created_at"}
                    if compare_created_at:
                        comparable_fields.add("created_at")
                    if any(
                        existing.get(field) != record[field]
                        for field in comparable_fields
                    ):
                        raise EvidenceImmutableError(
                            f"Evidence ID collision would mutate immutable record {evidence_id!r}"
                        )
                    return evidence_id
                fields = [field for field in self.EVIDENCE_FIELDS if field in record]
                assignments = ", ".join(f"{field} = ?" for field in fields)
                values = [
                    self._json_serialize(record[field])
                    if field in self.EVIDENCE_JSON_FIELDS
                    else record[field]
                    for field in fields
                ]
                db.command("sql", f"INSERT INTO Evidence SET {assignments}", *values)
        return evidence_id

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self._database() as db:
            rows = self._rows(db.query("sql", "SELECT FROM Evidence WHERE id = ?", evidence_id))
        return self._evidence_record_to_dict(rows[0]) if rows else None

    def list_evidence(self, repo_id: str, *, limit: int = 10000) -> List[Dict[str, Any]]:
        with self._database() as db:
            records = self._rows(
                db.query(
                    "sql",
                    "SELECT FROM Evidence WHERE repo_id = ? "
                    "ORDER BY created_at ASC LIMIT ?",
                    repo_id,
                    limit,
                )
            )
        return [self._evidence_record_to_dict(record) for record in records]

    def update_evidence(self, evidence_id: str, **kwargs) -> bool:
        raise EvidenceImmutableError(f"Evidence {evidence_id!r} is immutable")

    def attach_evidence(
        self, belief_id: str, evidence_ids: List[str], *, repo_id: str
    ) -> None:
        with self._database() as db:
            with db.transaction():
                rows = self._rows(
                    db.query(
                        "sql", f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?", belief_id
                    )
                )
                if not rows or self._record_get(rows[0], "layer") != "semantic":
                    raise EvidenceReferenceError(
                        "Evidence can attach only to an existing semantic belief: "
                        f"{belief_id!r}"
                    )
                if self._record_get(rows[0], "repo_id") != repo_id:
                    raise EvidenceReferenceError(
                        "Belief and Evidence must share a repository"
                    )
                resolved = self._validate_belief_references(
                    db,
                    memory_id=belief_id,
                    layer="semantic",
                    repo_id=repo_id,
                    source_ids=[],
                    evidence_ids=evidence_ids,
                )
                existing_ids = set(self._belief_evidence_ids(db, belief_id))
                now = utc_now().isoformat()
                for evidence_id in resolved:
                    if evidence_id in existing_ids:
                        continue
                    edge_id = f"be-{belief_id}-{evidence_id}"
                    db.command(
                        "sql",
                        f"""
                        CREATE EDGE {self.BELIEF_EVIDENCE_EDGE}
                        FROM (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                        TO (SELECT FROM Evidence WHERE id = ?)
                        SET id = ?, belief_id = ?, evidence_id = ?, created_at = ?
                        """,
                        belief_id,
                        evidence_id,
                        edge_id,
                        belief_id,
                        evidence_id,
                        now,
                    )

    def _insert_record(
        self,
        type_name: str,
        record: Dict[str, Any],
        fields: List[str],
        json_fields: set[str] = None,
    ) -> None:
        json_fields = json_fields or set()
        field_names = [field for field in fields if field in record]
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [
            self._json_serialize(record[field]) if field in json_fields else record[field]
            for field in field_names
        ]
        with self._database() as db:
            with db.transaction():
                db.command("sql", f"INSERT INTO {type_name} SET {assignments}", *values)

    def _update_record(
        self,
        type_name: str,
        record_id: str,
        updates: Dict[str, Any],
        json_fields: set[str] = None,
    ) -> bool:
        if self._get_record(type_name, record_id) is None:
            return False
        json_fields = json_fields or set()
        field_names = [field for field, value in updates.items() if value is not None]
        if not field_names:
            return False
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [
            self._json_serialize(updates[field]) if field in json_fields else updates[field]
            for field in field_names
        ]
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {type_name} SET {assignments} WHERE id = ?",
                    *values,
                    record_id,
                )
        return True

    def _get_record(self, type_name: str, record_id: str):
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {type_name} WHERE id = ?", record_id))
            if not rows:
                return None
            return self._record_to_dict(
                rows[0],
                self.RECORD_FIELDS.get(type_name, ["id"]),
                self.RECORD_JSON_FIELDS.get(type_name, set()),
            )

    def _list_records(
        self,
        type_name: str,
        fields: List[str],
        *,
        json_fields: set[str] = None,
        filters: Dict[str, Any] = None,
        limit: int = 100000,
        order_by: str = "created_at DESC",
    ) -> List[Dict[str, Any]]:
        query = f"SELECT FROM {type_name}"
        params = []
        conditions = []
        for field, value in (filters or {}).items():
            if value is not None:
                conditions.append(f"{field} = ?")
                params.append(value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {order_by} LIMIT ?"
        params.append(limit)
        with self._database() as db:
            rows = self._rows(db.query("sql", query, *params))
            return [self._record_to_dict(row, fields, json_fields or set()) for row in rows]

    def _delete_records(self, type_name: str, filters: Dict[str, Any]) -> int:
        records = self._list_records(type_name, ["id"], filters=filters, order_by="id ASC")
        with self._database() as db:
            with db.transaction():
                for record in records:
                    db.command("sql", f"DELETE FROM {type_name} WHERE id = ?", record["id"])
        return len(records)

    def _create_edge(
        self,
        edge_type: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        record: Dict[str, Any],
        fields: List[str],
    ) -> None:
        field_names = [field for field in fields if field in record]
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [record[field] for field in field_names]
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    CREATE EDGE {edge_type}
                    FROM (SELECT FROM {source_type} WHERE id = ?)
                    TO (SELECT FROM {target_type} WHERE id = ?)
                    SET {assignments}
                    """,
                    source_id,
                    target_id,
                    *values,
                )

    def _list_edge_records(self, edge_type: str, fields: List[str]) -> List[Dict[str, Any]]:
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {edge_type}"))
            return [self._record_to_dict(row, fields) for row in rows]

    def store_memory(
        self,
        content: str,
        layer: MemoryLayer = "episodic",
        repo_id: str = None,
        category: str = None,
        importance: float = 0.5,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        source_ids: List[str] = None,
        evidence_ids: List[str] = None,
        status: MemoryStatus = "active",
        epistemic_status: str = None,
        authority_attestation: str = None,
        replaces_belief_id: str = None,
        source: str = None,
        quality_flags: List[str] = None,
        embedding: List[float] = None,
        auto_link: bool = True,
        auto_link_limit: int = 3,
        auto_link_min_score: float = 0.53,
        memory_id: str = None,
        created_at: str = None,
    ) -> str:
        # Enforce the secrets policy at the single choke point every write path funnels
        # through, so a new caller cannot opt out. See visp_memory.quality.secrets.
        try:
            content, quality_flags = redact_for_storage(
                content,
                quality_flags,
                reject_if_redacted=authority_attestation is not None,
            )
        except SecretBearingContentError as exc:
            raise ProhibitionAuthorityError(str(exc)) from exc
        requested_memory_id = memory_id
        memory_id = memory_id or self._generate_id(content)
        repo_id = repo_id or UNSCOPED_REPO_ID
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []
        evidence_ids = evidence_ids or []
        quality_flags = quality_flags or []
        created_at = created_at or utc_now().isoformat()
        belief_type = None
        if layer == "semantic":
            belief_type = normalize_belief_type(category or "fact")
            category = belief_type
            if belief_type == "hypothesis":
                if epistemic_status not in (None, EpistemicStatus.HYPOTHESIZED.value):
                    raise ValueError(
                        "a hypothesis must begin with hypothesized epistemic status"
                    )
                epistemic_status = EpistemicStatus.HYPOTHESIZED.value
                created_time = parse_utc(created_at)
                if created_time is None:
                    raise ValueError("created_at must be a valid timestamp")
                maximum_valid_to = created_time + timedelta(days=HYPOTHESIS_TTL_DAYS)
                supplied_valid_to = metadata.get("valid_to")
                if supplied_valid_to is None:
                    metadata = {**metadata, "valid_to": maximum_valid_to.isoformat()}
                else:
                    valid_to = parse_utc(supplied_valid_to)
                    if valid_to is None or valid_to > maximum_valid_to:
                        raise ValueError(
                            "hypothesis valid_to exceeds the seven-day maximum TTL"
                        )
                    metadata = {**metadata, "valid_to": valid_to.isoformat()}
            elif belief_type == "prohibition":
                if epistemic_status not in (None, EpistemicStatus.OBSERVED.value):
                    raise ValueError(
                        "a verified prohibition must begin with observed epistemic status"
                    )
                epistemic_status = EpistemicStatus.OBSERVED.value
            else:
                epistemic_status = normalize_epistemic_status(
                    epistemic_status or EpistemicStatus.INFERRED.value
                )
            if belief_type != "prohibition" and authority_attestation is not None:
                raise ValueError(
                    "authority attestation applies only to a prohibition belief"
                )
            if replaces_belief_id is not None and belief_type != "prohibition":
                raise ValueError(
                    "replaces_belief_id applies only to a prohibition belief"
                )
        elif epistemic_status is not None:
            raise ValueError("epistemic status applies only to a semantic belief")
        else:
            category = category or "general"
        if repo_id == UNSCOPED_REPO_ID:
            from visp_memory.core.trust import Provenance, with_provenance

            tags = with_provenance(tags, Provenance.UNKNOWN)
            source = Provenance.UNKNOWN.value
        now = created_at

        with self._database() as db:
            with db.transaction():
                if layer in ("episodic", "raw") and not evidence_ids:
                    evidence_id = f"ev-{uuid.uuid4().hex}"
                    evidence_record = {
                        "id": evidence_id,
                        "content": content,
                        "content_hash": LocalStorage._evidence_hash(content),
                        "repo_id": repo_id,
                        "evidence_type": "observation" if layer == "episodic" else "raw",
                        "provenance": source or "unknown",
                        "metadata": {"captured_memory_id": memory_id, "exact_input": True},
                        "created_at": now,
                    }
                    fields = list(self.EVIDENCE_FIELDS)
                    assignments = ", ".join(f"{field} = ?" for field in fields)
                    values = [
                        self._json_serialize(evidence_record[field])
                        if field in self.EVIDENCE_JSON_FIELDS
                        else evidence_record[field]
                        for field in fields
                    ]
                    db.command("sql", f"INSERT INTO Evidence SET {assignments}", *values)
                    evidence_ids = [evidence_id]

                resolved_evidence_ids = self._validate_belief_references(
                    db,
                    memory_id=memory_id,
                    layer=layer,
                    repo_id=repo_id,
                    source_ids=source_ids,
                    evidence_ids=evidence_ids,
                )
                verified_attestation = None
                if belief_type == "prohibition":
                    if authority_attestation is None:
                        raise ProhibitionAuthorityError(
                            "prohibition belief requires an authority attestation"
                        )
                    evidence_claim = []
                    for evidence_id in sorted(resolved_evidence_ids):
                        rows = self._rows(
                            db.query("sql", "SELECT FROM Evidence WHERE id = ?", evidence_id)
                        )
                        evidence_claim.append(
                            {
                                "id": evidence_id,
                                "content_hash": self._record_get(rows[0], "content_hash"),
                            }
                        )
                    verified_attestation = verify_prohibition_attestation(
                        authority_attestation,
                        content=content,
                        repo_id=repo_id,
                        metadata=metadata,
                        evidence=evidence_claim,
                        expected_replaces_belief_id=replaces_belief_id,
                    )
                    for stored in self._rows(
                        db.query("sql", "SELECT FROM AuthorityAttestation")
                    ):
                        if (
                            self._record_get(stored, "key_id")
                            != verified_attestation.key_id
                            or self._record_get(stored, "nonce")
                            != verified_attestation.nonce
                        ):
                            continue
                        if self._record_get(stored, "digest") != verified_attestation.digest:
                            raise ProhibitionAuthorityError(
                                "prohibition authority nonce was reused with different bytes"
                            )
                        stored_belief_id = self._record_get(stored, "belief_id")
                        if (
                            requested_memory_id is not None
                            and stored_belief_id != memory_id
                        ):
                            raise ProhibitionAuthorityError(
                                "prohibition attestation replay targets a different belief"
                            )
                        existing_rows = self._rows(
                            db.query(
                                "sql",
                                f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?",
                                stored_belief_id,
                            )
                        )
                        existing_evidence = self._belief_evidence_ids(
                            db, stored_belief_id
                        )
                        if existing_rows:
                            existing = self._memory_record_to_dict(existing_rows[0])
                            if (
                                existing.get("content") == content
                                and existing.get("layer") == layer
                                and existing.get("repo_id") == repo_id
                                and existing.get("belief_type") == belief_type
                                and existing.get("epistemic_status") == epistemic_status
                                and existing.get("metadata") == metadata
                                and existing.get("status") == status
                                and existing_evidence == sorted(resolved_evidence_ids)
                            ):
                                return stored_belief_id
                        raise ProhibitionAuthorityError(
                            "prohibition attestation replay does not match the stored belief"
                        )
                db.command(
                    "sql",
                    f"""
                    INSERT INTO {self.MEMORY_TYPE} SET
                    id = ?, content = ?, layer = ?, repo_id = ?, category = ?,
                    belief_type = ?, epistemic_status = ?,
                    importance = ?, tags = ?, metadata = ?, source_ids = ?,
                    status = ?, source = ?, quality_flags = ?, created_at = ?,
                    accessed_at = ?, access_count = ?
                    """,
                    memory_id,
                    content,
                    layer,
                    repo_id,
                    category,
                    belief_type,
                    epistemic_status,
                    importance,
                    self._json_serialize(tags),
                    self._json_serialize(metadata),
                    self._json_serialize(source_ids),
                    status,
                    source,
                    self._json_serialize(quality_flags),
                    now,
                    now,
                    0,
                )
                for evidence_id in resolved_evidence_ids:
                    edge_id = f"be-{memory_id}-{evidence_id}"
                    db.command(
                        "sql",
                        f"""
                        CREATE EDGE {self.BELIEF_EVIDENCE_EDGE}
                        FROM (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                        TO (SELECT FROM Evidence WHERE id = ?)
                        SET id = ?, belief_id = ?, evidence_id = ?, created_at = ?
                        """,
                        memory_id,
                        evidence_id,
                        edge_id,
                        memory_id,
                        evidence_id,
                        now,
                    )
                if verified_attestation is not None:
                    attestation_id = f"att-{verified_attestation.digest}"
                    fields = self.AUTHORITY_ATTESTATION_FIELDS
                    db.command(
                        "sql",
                        "INSERT INTO AuthorityAttestation SET "
                        + ", ".join(f"{field} = ?" for field in fields),
                        attestation_id,
                        memory_id,
                        verified_attestation.key_id,
                        verified_attestation.nonce,
                        verified_attestation.digest,
                        verified_attestation.envelope,
                        now,
                    )
                    edge_id = f"ba-{memory_id}-{attestation_id}"
                    db.command(
                        "sql",
                        f"""
                        CREATE EDGE {self.BELIEF_AUTHORITY_EDGE}
                        FROM (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                        TO (SELECT FROM AuthorityAttestation WHERE id = ?)
                        SET id = ?, belief_id = ?, attestation_id = ?, created_at = ?
                        """,
                        memory_id,
                        attestation_id,
                        edge_id,
                        memory_id,
                        attestation_id,
                        now,
                    )

        return memory_id

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        record = self._query_memory(memory_id)
        if record is None:
            return None

        # ``_query_memory`` already returns a normalized dict; reuse it directly.
        memory = record
        # Increment in SQL (not read-modify-write) so concurrent reads cannot lose an
        # update, matching ``log_recall_event`` and the SQLite backend.
        accessed_at = utc_now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {self.MEMORY_TYPE} "
                    "SET access_count = access_count + 1, accessed_at = ? WHERE id = ?",
                    accessed_at,
                    memory_id,
                )

        # Reflect the post-increment state in the returned dict (best-effort; the
        # authoritative count lives in the store).
        memory["access_count"] = int(memory.get("access_count") or 0) + 1
        memory["accessed_at"] = accessed_at
        return memory

    def search_memories(
        self,
        query: str,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        status: str = "active",
        **_kwargs,
    ) -> List[Dict[str, Any]]:
        query, _ = redact_for_storage(query, None)
        # When no layer is requested, exclude the 'raw' layer from search results
        # (canonical SQLite behavior: search only episodic/semantic/intent). An explicit
        # ``layer='raw'`` request is still honored. list_memories keeps all layers.
        # Push the exclusion into the query so raw rows do not fill the candidate cap
        # ahead of lower-importance episodic/semantic/intent matches.
        exclude_raw = layer is None
        candidates = self._query_memories(
            layer=layer,
            repo_id=repo_id,
            category=category,
            status=status,
            limit=max(limit * 4, 50),
            order_by="importance DESC",
            exclude_raw=exclude_raw,
        )
        terms = [term.lower() for term in query.split() if term.strip()]
        results = []
        for memory in candidates:
            if float(memory.get("importance") or 0.0) < min_importance:
                continue
            content = str(memory.get("content", ""))
            if terms and not any(term in content.lower() for term in terms):
                continue
            memory["similarity"] = text_similarity(query, content)
            results.append(memory)

        return rank_memory_results(results, query=query, limit=limit)

    def list_memories(
        self,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at DESC",
    ) -> List[Dict[str, Any]]:
        return self._query_memories(
            layer=layer,
            repo_id=repo_id,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        existing = self._query_memory(memory_id)
        if existing is None:
            return False
        if kwargs.get("content") is not None:
            if existing.get("layer") == "semantic":
                from visp_memory.core.storage import SemanticMemoryImmutableError

                raise SemanticMemoryImmutableError(
                    "Semantic belief content is immutable; create an evidence-backed "
                    "successor with revise_memory"
                )
            original_content = kwargs["content"]
            kwargs["content"], redaction_flags = redact_for_storage(
                original_content,
                kwargs.get("quality_flags")
                if kwargs.get("quality_flags") is not None
                else existing.get("quality_flags") or [],
            )
            if kwargs["content"] != original_content:
                kwargs["quality_flags"] = redaction_flags

        allowed_fields = {
            "content",
            "importance",
            "tags",
            "metadata",
            "status",
            "approved_by",
            "approved_at",
            "archived_at",
            "source",
            "quality_flags",
        }
        updates = []
        params: list[Any] = []
        for field in allowed_fields:
            if field not in kwargs or kwargs[field] is None:
                continue
            value = kwargs[field]
            if field in self.MEMORY_JSON_FIELDS:
                value = self._json_serialize(value)
            updates.append(f"{field} = ?")
            params.append(value)

        if kwargs.get("status") == "archived" and "archived_at" not in kwargs:
            updates.append("archived_at = ?")
            params.append(utc_now().isoformat())
        elif kwargs.get("status") == "active" and "archived_at" not in kwargs:
            updates.append("archived_at = ?")
            params.append(None)

        if not updates:
            return False

        params.append(memory_id)
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {self.MEMORY_TYPE} SET {', '.join(updates)} WHERE id = ?",
                    *params,
                )
        return True

    def delete_memory(self, memory_id: str) -> bool:
        if self._query_memory(memory_id) is None:
            return False
        with self._database() as db:
            with db.transaction():
                db.command("sql", f"DELETE FROM {self.MEMORY_TYPE} WHERE id = ?", memory_id)
        return True

    def get_collection(self, layer: str):
        return None

    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        intent_id = self._generate_id(description)
        now = utc_now().isoformat()
        self._insert_record(
            "Intent",
            {
                "id": intent_id,
                "description": description,
                "priority": priority,
                "context": context or {},
                "repo_id": repo_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
            self.INTENT_FIELDS,
            self.RECORD_JSON_FIELDS["Intent"],
        )
        return intent_id

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        filters = {}
        if status and status != "all":
            filters["status"] = status
        if repo_id:
            filters["repo_id"] = repo_id
        intents = self._list_records(
            "Intent",
            self.INTENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["Intent"],
            filters=filters,
            order_by="priority DESC",
        )
        intents.sort(
            key=lambda item: (item.get("priority") or 0, item.get("created_at") or ""),
            reverse=True,
        )
        return intents

    def complete_intent(self, intent_id: str) -> bool:
        return False

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        updates = {
            key: kwargs[key]
            for key in ("description", "priority", "context")
            if key in kwargs and kwargs[key] is not None
        }
        if updates:
            updates["updated_at"] = utc_now().isoformat()
        return self._update_record(
            "Intent",
            intent_id,
            updates,
            json_fields=self.RECORD_JSON_FIELDS["Intent"],
        )

    def append_intent_outcome(
        self, intent_id: str, outcome: Dict[str, Any]
    ) -> bool:
        """Append with a compare-and-swap over the serialized context value."""
        with self._intent_outcome_lock:
            return self._append_intent_outcome_locked(intent_id, outcome)

    def _append_intent_outcome_locked(
        self, intent_id: str, outcome: Dict[str, Any]
    ) -> bool:
        """Run the CAS without overlapping embedded database handles."""
        for _attempt in range(20):
            with self._database() as db:
                rows = self._rows(
                    db.query(
                        "sql",
                        "SELECT context FROM Intent WHERE id = ?",
                        intent_id,
                    )
                )
                if not rows:
                    return False
                serialized = self._record_get(rows[0], "context")
                context = self._json_deserialize(serialized) or {}
                history = context.get("outcome_history")
                history = list(history) if isinstance(history, list) else []
                history.append(dict(outcome))
                context["outcome_history"] = history
                with db.transaction():
                    updated = self._rows(
                        db.command(
                            "sql",
                            "UPDATE Intent SET context = ?, updated_at = ? "
                            "WHERE id = ? AND context = ?",
                            self._json_serialize(context),
                            utc_now().isoformat(),
                            intent_id,
                            serialized,
                        )
                    )
                if sum(
                    int(self._record_get(item, "count", 0) or 0)
                    for item in updated
                ) > 0:
                    return True
        raise RuntimeError("Concurrent intent outcome append did not converge")

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        source = self._query_memory(source_id)
        target = self._query_memory(target_id)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        source_memory = self._memory_record_to_dict(source)
        target_memory = self._memory_record_to_dict(target)
        if source_memory.get("repo_id") != target_memory.get("repo_id"):
            raise ValueError("Memory relationships cannot cross repository boundaries")

        relationship_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")
        created_at = utc_now().isoformat()
        evidence_data = LocalStorage._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=created_at,
            legacy=False,
        )

        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    CREATE EDGE {self.MEMORY_RELATIONSHIP_EDGE}
                    FROM (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                    TO (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                    SET id = ?, source_id = ?, target_id = ?, relationship = ?,
                    strength = ?, confidence = ?, confidence_score = ?, source = ?,
                    source_file = ?, source_location = ?, reason = ?, created_by = ?,
                    created_at = ?
                    """,
                    source_id,
                    target_id,
                    relationship_id,
                    source_id,
                    target_id,
                    relationship,
                    strength,
                    evidence_data["confidence"],
                    evidence_data["confidence_score"],
                    evidence_data["source"],
                    evidence_data["source_file"],
                    evidence_data["source_location"],
                    evidence_data["reason"],
                    evidence_data["created_by"],
                    evidence_data["created_at"],
                )

        return relationship_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        source = self._query_memory(memory_id)
        if source is None:
            return []
        source_memory = self._memory_record_to_dict(source)
        related = []
        seen_ids = set()
        for edge in self.get_all_relationships(repo_id=source_memory.get("repo_id")):
            if relationship and edge.get("relationship") != relationship:
                continue
            if edge.get("source_id") == memory_id:
                related_id = edge.get("target_id")
            elif edge.get("target_id") == memory_id:
                related_id = edge.get("source_id")
            else:
                continue
            if not related_id or related_id in seen_ids:
                continue
            record = self._query_memory(related_id)
            if record is None:
                continue
            memory = self._memory_record_to_dict(record)
            memory["relationship"] = edge.get("relationship")
            memory["strength"] = edge.get("strength")
            memory["relationship_evidence"] = edge.get("evidence")
            related.append(memory)
            seen_ids.add(related_id)
        return related

    def start_session(
        self,
        *,
        owner_id: str = None,
        team_id: str = None,
        repo_id: str = None,
    ) -> str:
        session_id = self._generate_id("session")
        now = utc_now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"INSERT INTO {self.SESSION_TYPE} SET id = ?, owner_id = ?, "
                    "team_id = ?, repo_id = ?, started_at = ?",
                    session_id,
                    owner_id,
                    team_id,
                    repo_id,
                    now,
                )
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._database() as db:
            records = self._rows(
                db.query(
                    "sql",
                    f"SELECT FROM {self.SESSION_TYPE} WHERE id = ?",
                    session_id,
                )
            )
        if not records:
            return None
        record = records[0]
        return {
            "id": self._record_get(record, "id"),
            "owner_id": self._record_get(record, "owner_id"),
            "team_id": self._record_get(record, "team_id"),
            "repo_id": self._record_get(record, "repo_id"),
            "summary": self._record_get(record, "summary"),
            "memory_ids": self._json_deserialize(
                self._record_get(record, "memory_ids")
            ) or [],
            "started_at": self._record_get(record, "started_at"),
            "ended_at": self._record_get(record, "ended_at"),
        }

    def end_session(
        self, session_id: str, summary: str, memory_ids: List[str]
    ) -> SessionCompletionStatus:
        ended_at = utc_now().isoformat()
        with self._database() as db:
            with db.transaction():
                updated = self._rows(
                    db.command(
                        "sql",
                        f"""
                    UPDATE {self.SESSION_TYPE}
                    SET summary = ?, memory_ids = ?, ended_at = ?
                    WHERE id = ? AND ended_at IS NULL
                    RETURN AFTER @this
                    """,
                        summary,
                        self._json_serialize(memory_ids),
                        ended_at,
                        session_id,
                    )
                )
                if updated:
                    return SessionCompletionStatus.COMPLETED
                records = self._rows(
                    db.query(
                        "sql",
                        f"SELECT FROM {self.SESSION_TYPE} WHERE id = ?",
                        session_id,
                    )
                )
                return (
                    SessionCompletionStatus.ALREADY_COMPLETED
                    if records
                    else SessionCompletionStatus.NOT_FOUND
                )

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {self.MEMORY_RELATIONSHIP_EDGE}"))
            relationships = [self._relationship_record_to_dict(row) for row in rows]
        if not repo_id:
            return relationships

        filtered = []
        for relationship in relationships:
            source = self._query_memory(relationship.get("source_id"))
            target = self._query_memory(relationship.get("target_id"))
            if source is None or target is None:
                continue
            source_memory = self._memory_record_to_dict(source)
            target_memory = self._memory_record_to_dict(target)
            if source_memory.get("repo_id") == repo_id and target_memory.get("repo_id") == repo_id:
                filtered.append(relationship)
        return filtered

    def delete_relationship(self, relationship_id: str) -> bool:
        with self._database() as db:
            existing = list(
                db.query(
                    "sql",
                    f"SELECT FROM {self.MEMORY_RELATIONSHIP_EDGE} WHERE id = ?",
                    relationship_id,
                )
            )
            if not existing:
                return False
            with db.transaction():
                db.command(
                    "sql",
                    f"DELETE EDGE {self.MEMORY_RELATIONSHIP_EDGE} WHERE id = ?",
                    relationship_id,
                )
        return True

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        memories = self.list_memories(repo_id=repo_id, status="active", limit=100000)
        by_layer: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for memory in memories:
            layer = str(memory.get("layer") or "episodic")
            category = str(memory.get("category") or "general")
            by_layer[layer] = by_layer.get(layer, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1

        return {
            "memories_by_layer": by_layer,
            "memories_by_category": by_category,
            "total_memories": len(memories),
            "active_intents": len(self.get_active_intents(repo_id=repo_id)),
            "total_relationships": len(self.get_all_relationships(repo_id=repo_id)),
        }

    def store_repository(self, repo: Dict[str, Any]) -> str:
        repo_id = repo.get("id") or self._generate_id(repo["name"])
        if self.get_repository(repo_id) is not None:
            raise ValueError(f"Repository already exists: {repo_id}")
        self._insert_record(
            "Repository",
            {
                "id": repo_id,
                "name": repo["name"],
                "url": repo.get("url"),
                "description": repo.get("description"),
                "tech_stack": repo.get("tech_stack", []),
                "team_id": repo.get("team_id"),
                "metadata": repo.get("metadata", {}),
                "status": repo.get("status", "active"),
                # Persist creation time once at T0 so reads round-trip a real timestamp, matching
                # the neo4j and sqlite backends (was absent, so the router fabricated utc_now() on
                # every read).
                "created_at": repo.get("created_at") or utc_now().isoformat(),
            },
            self.REPOSITORY_FIELDS,
            self.RECORD_JSON_FIELDS["Repository"],
        )
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("Repository", repo_id)
        if record is None:
            return None
        return self._record_to_dict(
            record,
            self.REPOSITORY_FIELDS,
            self.RECORD_JSON_FIELDS["Repository"],
        )

    def list_repositories(
        self, team_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        filters = {}
        if team_id:
            filters["team_id"] = team_id
        if status and status != "all":
            filters["status"] = status
        return self._list_records(
            "Repository",
            self.REPOSITORY_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["Repository"],
            filters=filters or None,
            order_by="name ASC",
        )

    def update_repository(self, repo_id: str, **kwargs) -> bool:
        allowed = {"name", "url", "description", "tech_stack", "metadata", "status"}
        updates = {
            key: value
            for key, value in kwargs.items()
            if key in allowed and value is not None
        }
        if updates.get("status") == "archived":
            updates["archived_at"] = utc_now().isoformat()
        elif updates.get("status") == "active":
            updates["archived_at"] = None
        return self._update_record(
            "Repository",
            repo_id,
            updates,
            json_fields=self.RECORD_JSON_FIELDS["Repository"],
        )

    def delete_repository(self, repo_id: str) -> bool:
        if self.get_repository(repo_id) is None:
            return False
        self._delete_records("Memory", {"repo_id": repo_id})
        self._delete_records("Intent", {"repo_id": repo_id})
        self._delete_records("Repository", {"id": repo_id})
        return True

    def list_project_ids(self) -> List[str]:
        memories = self.list_memories(status="all", limit=100000)
        repo_ids = {
            str(memory["repo_id"]) for memory in memories if memory.get("repo_id") not in (None, "")
        }
        repo_ids.update(
            intent["repo_id"]
            for intent in self.get_active_intents(status="all")
            if intent.get("repo_id") not in (None, "")
        )
        repo_ids.update(repo["id"] for repo in self.list_repositories() if repo.get("id"))
        return sorted(repo_ids)

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        if self.get_repository(source_id) is None:
            raise ValueError(f"Repository not found: {source_id}")
        if self.get_repository(target_id) is None:
            raise ValueError(f"Repository not found: {target_id}")
        dependency_id = self._generate_id(f"{source_id}-{target_id}-{dep_type}")
        self._create_edge(
            self.REPO_DEPENDENCY_EDGE,
            "Repository",
            source_id,
            "Repository",
            target_id,
            {
                "id": dependency_id,
                "source_repo_id": source_id,
                "target_repo_id": target_id,
                "type": dep_type,
                "version": version,
                "notes": notes,
            },
            self.REPO_DEPENDENCY_FIELDS,
        )
        return dependency_id

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "target_id": dependency.get("target_repo_id"),
                "type": dependency.get("type"),
                "version": dependency.get("version"),
                "notes": dependency.get("notes"),
            }
            for dependency in self._list_edge_records(
                self.REPO_DEPENDENCY_EDGE, self.REPO_DEPENDENCY_FIELDS
            )
            if dependency.get("source_repo_id") == repo_id
        ]

    def store_user(self, user: Dict[str, Any]) -> str:
        user_id = user["id"]
        if self.get_user(user_id) is not None:
            raise ValueError(f"User already exists: {user_id}")
        self._insert_record(
            "User",
            {
                "id": user_id,
                "username": user["username"],
                "email": user.get("email"),
                "display_name": user.get("display_name"),
                "metadata": user.get("metadata", {}),
            },
            self.USER_FIELDS,
            self.RECORD_JSON_FIELDS["User"],
        )
        return user_id

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("User", user_id)
        if record is None:
            return None
        return self._record_to_dict(record, self.USER_FIELDS, self.RECORD_JSON_FIELDS["User"])

    def store_team(self, team: Dict[str, Any]) -> str:
        team_id = team["id"]
        if self.get_team(team_id) is not None:
            raise ValueError(f"Team already exists: {team_id}")
        self._insert_record(
            "Team",
            {
                "id": team_id,
                "name": team["name"],
                "description": team.get("description"),
                "metadata": team.get("metadata", {}),
            },
            self.TEAM_FIELDS,
            self.RECORD_JSON_FIELDS["Team"],
        )
        return team_id

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("Team", team_id)
        if record is None:
            return None
        return self._record_to_dict(record, self.TEAM_FIELDS, self.RECORD_JSON_FIELDS["Team"])

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        if self.get_team(team_id) is None or self.get_user(user_id) is None:
            return False
        membership_id = self._generate_id(f"{team_id}-{user_id}")
        self._create_edge(
            self.TEAM_MEMBER_EDGE,
            "Team",
            team_id,
            "User",
            user_id,
            {"id": membership_id, "team_id": team_id, "user_id": user_id},
            self.TEAM_MEMBER_FIELDS,
        )
        return True

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        teams = []
        seen_ids = set()
        for membership in self._list_edge_records(self.TEAM_MEMBER_EDGE, self.TEAM_MEMBER_FIELDS):
            if membership.get("user_id") != user_id:
                continue
            team_id = membership.get("team_id")
            if not team_id or team_id in seen_ids:
                continue
            team = self.get_team(team_id)
            if team is not None:
                teams.append(team)
                seen_ids.add(team_id)
        return teams

    def append_audit_log(
        self,
        event_type: str,
        actor_id: str = None,
        repo_id: str = None,
        target_type: str = None,
        target_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        audit_id = self._generate_id(f"{event_type}:{target_id or ''}")
        self._insert_record(
            "AuditLog",
            {
                "id": audit_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "repo_id": repo_id,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": metadata or {},
                "created_at": utc_now().isoformat(),
            },
            self.AUDIT_FIELDS,
            self.RECORD_JSON_FIELDS["AuditLog"],
        )
        return audit_id

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._list_records(
            "AuditLog",
            self.AUDIT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["AuditLog"],
            filters={"actor_id": actor_id, "repo_id": repo_id, "event_type": event_type},
            limit=limit,
            order_by="created_at DESC",
        )

    def log_recall_event(
        self,
        memory_id: str,
        event_type: str,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        memory = self._query_memory(memory_id)
        if memory is None:
            raise ValueError(f"Memory not found: {memory_id}")
        memory_data = self._memory_record_to_dict(memory)
        normalized_type = LocalStorage._normalize_recall_event_type(event_type)
        event_id = self._generate_id(f"{memory_id}:{normalized_type}")
        self._insert_record(
            "RecallFeedback",
            {
                "id": event_id,
                "memory_id": memory_id,
                "event_type": normalized_type,
                "repo_id": repo_id if repo_id is not None else memory_data.get("repo_id"),
                "query_hash": LocalStorage._hash_recall_query(query),
                "task_id": task_id,
                "outcome": outcome,
                "metadata": LocalStorage._sanitize_recall_metadata(metadata),
                "created_at": utc_now().isoformat(),
            },
            self.RECALL_EVENT_FIELDS,
            self.RECORD_JSON_FIELDS["RecallFeedback"],
        )
        # Retrieval-induced strengthening, kept in parity with LocalStorage so the
        # activation boost and spaced-repetition decay behave the same across backends.
        # Increment in SQL (not read-modify-write) so concurrent use events cannot lose
        # an update, matching SQLite's `access_count = access_count + 1`.
        if normalized_type in REINFORCING_RECALL_EVENTS:
            with self._database() as db:
                with db.transaction():
                    db.command(
                        "sql",
                        f"UPDATE {self.MEMORY_TYPE} "
                        "SET access_count = access_count + 1, accessed_at = ? WHERE id = ?",
                        utc_now().isoformat(),
                        memory_id,
                    )
        return event_id

    def inspect_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        filters = {
            "memory_id": memory_id,
            "repo_id": repo_id,
            "event_type": LocalStorage._normalize_recall_event_type(event_type)
            if event_type
            else None,
        }
        events = self._list_records(
            "RecallFeedback",
            self.RECALL_EVENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["RecallFeedback"],
            filters=filters,
            limit=limit,
            order_by="created_at DESC",
        )
        all_events = self._list_records(
            "RecallFeedback",
            self.RECALL_EVENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["RecallFeedback"],
            filters=filters,
            limit=100000,
            order_by="created_at DESC",
        )
        by_event_type: dict[str, int] = {}
        signals_by_memory: dict[str, dict[str, Any]] = {}
        for event in all_events:
            event_type_value = event.get("event_type")
            by_event_type[event_type_value] = by_event_type.get(event_type_value, 0) + 1
            signal = signals_by_memory.setdefault(
                event["memory_id"],
                {
                    "memory_id": event["memory_id"],
                    "repo_id": event.get("repo_id"),
                    "counts": {},
                    "total_events": 0,
                    "last_event_at": event.get("created_at"),
                },
            )
            signal["counts"][event_type_value] = signal["counts"].get(event_type_value, 0) + 1
            signal["total_events"] += 1
            signal["last_event_at"] = max(
                signal.get("last_event_at") or "", event.get("created_at") or ""
            )

        signals = []
        for signal in signals_by_memory.values():
            utility_score = LocalStorage._recall_utility_score_from_counts(signal["counts"])
            signal["utility_score"] = utility_score
            signal["utility_rank_adjustment"] = utility_rank_adjustment(utility_score)
            signals.append(signal)
        signals.sort(
            key=lambda item: (item.get("utility_score", 0.0), item.get("last_event_at") or ""),
            reverse=True,
        )
        return {
            "summary": {
                "total_events": sum(by_event_type.values()),
                "by_event_type": by_event_type,
                "memories": len(signals),
            },
            "signals": signals,
            "events": events,
        }

    def reset_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> int:
        filters = {
            "memory_id": memory_id,
            "repo_id": repo_id,
            "event_type": LocalStorage._normalize_recall_event_type(event_type)
            if event_type
            else None,
        }
        return self._delete_records("RecallFeedback", filters)


__all__ = [
    "ARCADEDB_INSTALL_MESSAGE",
    "ArcadeDbDependencyError",
    "ArcadeDbStorage",
    "load_arcadedb_driver",
]
