"""Small transactional persistence boundary for the shared dreaming policy."""

import json
from contextlib import contextmanager

from visp_memory.core.storage import LocalStorage

TABLES = {
    "projects": ("dream_projects", "DreamProject", ("repo_id",)),
    "runs": ("dream_runs", "DreamRun", ("id",)),
    "actions": ("dream_actions", "DreamAction", ("id",)),
    "dismissals": ("dream_dismissals", "DreamDismissal", ("repo_id", "proposal_id", "signature")),
}


def defaults(repo_id):
    return dict(
        repo_id=repo_id, enabled=False, interval_hours=24, next_run=None, last_error=None, cursor=""
    )


class SQLiteUnit:
    def __init__(self, storage, connection):
        self.storage, self.connection = storage, connection

    def rows(self, kind, *, newest=False, limit=None, **filters):
        table = TABLES[kind][0]
        where = " AND ".join(f"{key} = ?" for key in filters) or "1"
        suffix = " ORDER BY created_at DESC" if newest else ""
        parameters = tuple(filters.values())
        if limit is not None:
            suffix += " LIMIT ?"
            parameters += (limit,)
        return [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM {table} WHERE {where}{suffix}", parameters
            )
        ]

    def put(self, kind, record):
        table, _, keys = TABLES[kind]
        fields = list(record)
        update = ", ".join(f"{k}=excluded.{k}" for k in fields if k not in keys)
        conflict = f"DO UPDATE SET {update}" if update else "DO NOTHING"
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(fields)}) "
            f"VALUES ({', '.join('?' for _ in fields)}) "
            f"ON CONFLICT({', '.join(keys)}) {conflict}",
            tuple(record.values()),
        )

    def project(self, repo_id):
        row = self.connection.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        return dict(row) if row else None

    def memories(self, repo_id, cursor, limit):
        return [
            self.storage._row_to_dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memories WHERE repo_id = ? AND status = 'active' "
                "AND layer IN ('episodic', 'semantic') AND id > ? ORDER BY id LIMIT ?",
                (repo_id, cursor, limit),
            )
        ]

    def memory(self, memory_id):
        row = self.connection.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self.storage._row_to_dict(row) if row else None

    def change_memory(self, memory_id, values):
        self.connection.execute(
            "UPDATE memories SET status = ?, metadata = ?, archived_at = ? WHERE id = ?",
            (values["status"], json.dumps(values["metadata"]), values["archived_at"], memory_id),
        )

    def linked_ids(self, memory_ids):
        from visp_memory.core.dreaming.journal import linked_ids

        return linked_ids(self.connection, memory_ids)


class Neo4jUnit:
    def __init__(self, storage, transaction):
        self.storage, self.transaction = storage, transaction

    def rows(self, kind, *, newest=False, limit=None, **filters):
        label = TABLES[kind][1]
        where = " AND ".join(f"n.{key} = ${key}" for key in filters) or "true"
        suffix = " ORDER BY n.created_at DESC" if newest else ""
        if limit is not None:
            suffix += " LIMIT $limit"
            filters["limit"] = limit
        return [
            dict(row["n"])
            for row in self.transaction.run(
                f"MATCH (n:{label}) WHERE {where} RETURN n{suffix}", **filters
            )
        ]

    def put(self, kind, record):
        _, label, keys = TABLES[kind]
        match = ", ".join(f"{key}: ${key}" for key in keys)
        self.transaction.run(
            f"MERGE (n:{label} {{{match}}}) SET n += $record",
            record=record,
            **{k: record[k] for k in keys},
        ).consume()

    def project(self, repo_id):
        row = self.transaction.run("MATCH (r:Repository {id: $id}) RETURN r", id=repo_id).single()
        return dict(row["r"]) if row else None

    def memories(self, repo_id, cursor, limit):
        return [
            self.storage._memory_node_to_dict(dict(row["m"]))
            for row in self.transaction.run(
                "MATCH (m:Memory {repo_id: $repo, status: 'active'}) "
                "WHERE m.layer IN ['episodic', 'semantic'] AND m.id > $cursor "
                "RETURN m ORDER BY m.id LIMIT $limit",
                repo=repo_id,
                cursor=cursor,
                limit=limit,
            )
        ]

    def memory(self, memory_id):
        row = self.transaction.run("MATCH (m:Memory {id: $id}) RETURN m", id=memory_id).single()
        return self.storage._memory_node_to_dict(dict(row["m"])) if row else None

    def change_memory(self, memory_id, values):
        self.transaction.run(
            "MATCH (m:Memory {id: $id}) SET m += $values",
            id=memory_id,
            values={**values, "metadata": json.dumps(values["metadata"])},
        ).consume()

    def linked_ids(self, memory_ids):
        rows = self.transaction.run(
            "MATCH (a:Memory)-[]->(b:Memory) WHERE a.id IN $ids OR b.id IN $ids "
            "RETURN a.id AS source, b.id AS target",
            ids=memory_ids,
        )
        linked = {row[key] for row in rows for key in ("source", "target")}
        linked.update(
            row["id"]
            for row in self.transaction.run(
                "MATCH (m:Memory) UNWIND m.source_ids AS id WITH id "
                "WHERE id IN $ids RETURN DISTINCT id",
                ids=memory_ids,
            )
        )
        return linked


@contextmanager
def transaction(storage, *, write=False):
    if isinstance(storage, LocalStorage):
        with storage._get_db() as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield SQLiteUnit(storage, connection)
            if write:
                connection.commit()
    else:
        from visp_memory.core.neo4j_governance import lock_graph

        with storage.driver.session() as session, session.begin_transaction() as tx:
            # Preview also takes the lock to read a consistent graph and journal together.
            lock_graph(tx)
            yield Neo4jUnit(storage, tx)
            tx.commit()
