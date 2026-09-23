"""Transactional recall feedback and bounded retrieval signals for Neo4j."""

import json

from visp_memory.core.clock import utc_now_iso
from visp_memory.core.ranking import utility_rank_adjustment
from visp_memory.core.storage import REINFORCING_RECALL_EVENTS, LocalStorage

SAME_SCOPE = "(e.repo_id = m.repo_id OR (e.repo_id IS NULL AND m.repo_id IS NULL))"


def _feedback_match(memory_id, repo_id, event_type, *, canonical=True):
    conditions, params = [], {}
    if memory_id is not None:
        conditions.append("e.memory_id = $memory_id")
        params["memory_id"] = memory_id
    elif repo_id is not None:
        conditions.append("m.repo_id = $repo_id")
        params["repo_id"] = repo_id
    if event_type:
        conditions.append("e.event_type = $event_type")
        params["event_type"] = LocalStorage._normalize_recall_event_type(event_type)
    # Unscoped inspection/reset intentionally includes raw history, as in SQLite.
    if canonical and (memory_id is not None or repo_id is not None):
        conditions.extend(["m IS NOT NULL", SAME_SCOPE])
    query = (
        "MATCH (e:RecallFeedback) OPTIONAL MATCH (m:Memory {id: e.memory_id}) "
        "WITH e, m WHERE " + (" AND ".join(conditions) or "true")
    )
    return query, params


def _summarize(rows):
    by_type, by_memory = {}, {}
    for row in rows:
        kind, count = row["event_type"], row["count"]
        by_type[kind] = by_type.get(kind, 0) + count
        signal = by_memory.setdefault(row["memory_id"], {
            "memory_id": row["memory_id"], "repo_id": row["repo_id"],
            "counts": {}, "total_events": 0, "last_event_at": "",
        })
        signal["counts"][kind] = signal["counts"].get(kind, 0) + count
        signal["total_events"] += count
        signal["last_event_at"] = max(signal["last_event_at"], row["last_event_at"] or "")
    for signal in by_memory.values():
        score = LocalStorage._recall_utility_score_from_counts(signal["counts"])
        signal.update(utility_score=score, utility_rank_adjustment=utility_rank_adjustment(score))
    signals = sorted(
        by_memory.values(), key=lambda s: (s["utility_score"], s["last_event_at"]), reverse=True,
    )
    return {
        "summary": {"total_events": sum(by_type.values()), "by_event_type": by_type,
                    "memories": len(signals)},
        "signals": signals,
    }


class Neo4jFeedback:
    FEEDBACK_NODE_FIELDS = {
        "id", "memory_id", "event_type", "repo_id", "query_hash", "task_id",
        "outcome", "metadata", "created_at",
    }

    def _ensure_feedback_indexes(self):
        with self.driver.session() as session:
            session.run(
                "CREATE CONSTRAINT recall_feedback_id IF NOT EXISTS "
                "FOR (e:RecallFeedback) REQUIRE e.id IS UNIQUE"
            ).consume()
            session.run(
                "CREATE INDEX recall_feedback_memory IF NOT EXISTS "
                "FOR (e:RecallFeedback) ON (e.memory_id)"
            ).consume()
            session.run(
                "CREATE INDEX recall_feedback_type IF NOT EXISTS "
                "FOR (e:RecallFeedback) ON (e.event_type)"
            ).consume()

    @staticmethod
    def _feedback_scope(tx, memory_id, repo_id):
        """Validate without counting a feedback operation as a memory access."""
        if memory_id is None:
            return repo_id
        row = tx.run(
            "MATCH (m:Memory {id: $id}) RETURN m.repo_id AS repo_id", id=memory_id,
        ).single()
        if row is None:
            raise ValueError(f"Memory not found: {memory_id}")
        canonical = row["repo_id"]
        if repo_id is not None and repo_id != canonical:
            raise ValueError(
                f"Recall utility repository mismatch for memory {memory_id!r}: "
                f"memory belongs to {canonical!r}, caller supplied {repo_id!r}"
            )
        return canonical

    def log_recall_event(
        self, memory_id, event_type, repo_id=None, query=None, task_id=None,
        outcome=None, metadata=None,
    ):
        if memory_id is None:
            raise ValueError("Memory not found: None")
        kind = LocalStorage._normalize_recall_event_type(event_type)
        event = {
            "id": self._generate_id(f"{memory_id}:{kind}"), "memory_id": memory_id,
            "event_type": kind, "query_hash": LocalStorage._hash_recall_query(query),
            "task_id": task_id, "outcome": outcome, "created_at": utc_now_iso(),
            "metadata": json.dumps(LocalStorage._sanitize_recall_metadata(metadata)),
        }
        # Share the governance lock with deletion/revision. Scope validation, append,
        # and reinforcement commit together, including across concurrent processes.
        with self._write_session() as tx:
            event["repo_id"] = self._feedback_scope(tx, memory_id, repo_id)
            tx.run(
                "MATCH (m:Memory {id: $memory_id}) CREATE (e:RecallFeedback) SET e = $event "
                "CREATE (e)-[:FEEDBACK_FOR]->(m) "
                "FOREACH (_ IN CASE WHEN $reinforce THEN [1] ELSE [] END | "
                "SET m.access_count = coalesce(m.access_count, 0) + 1, "
                "m.accessed_at = datetime())",
                memory_id=memory_id, event=event, reinforce=kind in REINFORCING_RECALL_EVENTS,
            ).consume()
        return event["id"]

    def inspect_recall_utility(self, memory_id=None, repo_id=None, event_type=None, limit=50):
        query, params = _feedback_match(memory_id, repo_id, event_type)
        with self.driver.session() as session, session.begin_transaction() as tx:
            self._feedback_scope(tx, memory_id, repo_id)
            report = _summarize(tx.run(
                query + " RETURN e.memory_id AS memory_id, e.repo_id AS repo_id, "
                "e.event_type AS event_type, count(*) AS count, "
                "max(e.created_at) AS last_event_at", **params,
            ))
            report["events"] = [
                self._normalize_node(
                    dict(row["e"]), fields=self.FEEDBACK_NODE_FIELDS, json_fields={"metadata"},
                    defaults={"metadata": {}, "query_hash": None, "task_id": None,
                              "outcome": None, "repo_id": None},
                )
                for row in tx.run(
                    query + " RETURN e ORDER BY e.created_at DESC, e.id DESC LIMIT $limit",
                    **params, limit=max(0, int(limit)),
                )
            ]
            report["verification"] = self._verify_feedback(tx, memory_id, repo_id, event_type)
            return report

    @staticmethod
    def _verify_feedback(tx, memory_id, repo_id, event_type):
        query, params = _feedback_match(memory_id, repo_id, event_type, canonical=False)
        checked, violations = 0, []
        for row in tx.run(
            query + " RETURN e.id AS event_id, e.memory_id AS memory_id, "
            "e.repo_id AS event_repo_id, m.id AS matched_memory_id, "
            "m.repo_id AS memory_repo_id ORDER BY e.created_at, e.id", **params,
        ):
            checked += 1
            if (row["matched_memory_id"] is not None
                    and row["event_repo_id"] != row["memory_repo_id"]):
                violations.append({key: row[key] for key in (
                    "event_id", "memory_id", "event_repo_id", "memory_repo_id",
                )})
        return {
            "valid": not violations, "checked_events": checked,
            "cross_repository_events": len(violations), "violations": violations,
        }

    def verify_recall_utility(self, memory_id=None, repo_id=None, event_type=None):
        with self.driver.session() as session, session.begin_transaction() as tx:
            self._feedback_scope(tx, memory_id, repo_id)
            return self._verify_feedback(tx, memory_id, repo_id, event_type)

    def reset_recall_utility(self, memory_id=None, repo_id=None, event_type=None):
        query, params = _feedback_match(memory_id, repo_id, event_type)
        with self._write_session() as tx:
            self._feedback_scope(tx, memory_id, repo_id)
            return tx.run(query + " DETACH DELETE e RETURN count(*) AS deleted", **params).single()[
                "deleted"
            ]

    def _attach_recall_utility_scores(self, memories):
        """Aggregate only these candidates; never issue one query per memory."""
        if not memories:
            return
        with self.driver.session() as session:
            rows = session.run(
                "MATCH (e:RecallFeedback) WHERE e.memory_id IN $ids "
                "MATCH (m:Memory {id: e.memory_id}) WHERE " + SAME_SCOPE + " "
                "RETURN e.memory_id AS memory_id, e.event_type AS event_type, count(*) AS count",
                ids=list({memory["id"] for memory in memories}),
            )
            counts = {}
            for row in rows:
                counts.setdefault(row["memory_id"], {})[row["event_type"]] = row["count"]
        for memory in memories:
            events = counts.get(memory["id"], {})
            score = LocalStorage._recall_utility_score_from_counts(events)
            memory["utility_score"] = score
            memory["utility_signal"] = {
                "counts": events, "total_events": sum(events.values()),
                "rank_adjustment": utility_rank_adjustment(score),
            }
