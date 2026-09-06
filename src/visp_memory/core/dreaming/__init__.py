"""Project-scoped dreaming: deterministic proposals and recoverable exact cleanup."""

import json
import secrets
from datetime import timedelta

from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.dreaming import journal
from visp_memory.core.dreaming.planner import SCAN_LIMIT, plan
from visp_memory.core.storage import LocalStorage


class Dreaming:
    def __init__(self, storage):
        if not isinstance(storage, LocalStorage):
            raise NotImplementedError("Dreaming currently requires a SQLite server")
        self.storage = storage
        with storage._get_db() as conn:
            conn.executescript(journal.SCHEMA)
            conn.commit()

    def _project(self, conn, repo_id):
        repo = conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,)).fetchone()
        if not repo or repo["status"] != "active":
            raise ValueError("Select an active, registered project")
        return repo

    def settings(self, repo_id):
        with self.storage._get_db() as conn:
            self._project(conn, repo_id)
            row = conn.execute(
                "SELECT * FROM dream_projects WHERE repo_id = ?", (repo_id,)
            ).fetchone()
            result = (
                dict(row)
                if row
                else {
                    "repo_id": repo_id,
                    "enabled": False,
                    "interval_hours": 24,
                    "next_run": None,
                    "last_error": None,
                    "cursor": "",
                }
            )
            result["enabled"] = bool(result["enabled"])
            return result

    def configure(self, repo_id, *, enabled, interval_hours=24):
        if not isinstance(enabled, bool) or interval_hours not in {6, 12, 24, 168}:
            raise ValueError("Choose an interval of 6, 12, 24, or 168 hours")
        with self.storage._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._project(conn, repo_id)
            next_run = (
                (utc_now() + timedelta(hours=interval_hours)).isoformat() if enabled else None
            )
            conn.execute(
                """INSERT INTO dream_projects(repo_id, enabled, interval_hours, next_run)
                VALUES (?, ?, ?, ?) ON CONFLICT(repo_id) DO UPDATE SET
                enabled=excluded.enabled, interval_hours=excluded.interval_hours,
                next_run=excluded.next_run, last_error=NULL""",
                (repo_id, enabled, interval_hours, next_run),
            )
            conn.commit()
        return self.settings(repo_id)

    def _plan(self, conn, repo_id, now):
        self._project(conn, repo_id)
        setting = conn.execute(
            "SELECT cursor FROM dream_projects WHERE repo_id = ?", (repo_id,)
        ).fetchone()
        cursor = setting["cursor"] if setting else ""
        rows = conn.execute(
            """SELECT * FROM memories WHERE repo_id = ? AND status = 'active'
            AND layer IN ('episodic', 'semantic') AND id > ? ORDER BY id LIMIT ?""",
            (repo_id, cursor, SCAN_LIMIT + 1),
        ).fetchall()
        memories = [self.storage._row_to_dict(row) for row in rows[:SCAN_LIMIT]]
        linked = journal.linked_ids(conn, [m["id"] for m in memories if m["layer"] == "episodic"])
        report = plan(memories, linked, now)
        dismissed = {
            (row["proposal_id"], row["signature"])
            for row in conn.execute(
                "SELECT proposal_id, signature FROM dream_dismissals WHERE repo_id = ?", (repo_id,)
            )
        }
        report["proposals"] = [
            p for p in report["proposals"] if (p["id"], journal.signature(p)) not in dismissed
        ]
        report.update(
            repo_id=repo_id,
            partial=len(rows) > SCAN_LIMIT or bool(cursor),
            next_cursor=memories[-1]["id"] if len(rows) > SCAN_LIMIT else "",
        )
        return report

    def preview(self, repo_id):
        with self.storage._get_db() as conn:
            conn.execute("BEGIN")
            return self._plan(conn, repo_id, utc_now())

    def run(self, repo_id, *, actor_id, scheduled=False):
        with self.storage._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = utc_now()
            setting = conn.execute(
                "SELECT * FROM dream_projects WHERE repo_id = ?", (repo_id,)
            ).fetchone()
            if scheduled and (
                not setting
                or not setting["enabled"]
                or (parse_utc(setting["next_run"]) or now) > now
            ):
                return None
            report = self._plan(conn, repo_id, now)
            run_id = "drm_" + secrets.token_hex(10)
            for item in report["proposals"]:
                if item["kind"] == "duplicate" and item["automatic"]:
                    item["action_id"] = journal.apply(
                        self.storage, conn, run_id, repo_id, item, actor_id
                    )
                    item["resolution"] = "applied"
                else:
                    item["resolution"] = "pending"
            report.update(
                id=run_id,
                created_at=now.isoformat(),
                status="completed",
                actor_id=actor_id,
                scheduled=scheduled,
            )
            conn.execute(
                "INSERT INTO dream_runs VALUES (?, ?, ?, ?)",
                (run_id, repo_id, now.isoformat(), json.dumps(report)),
            )
            interval = setting["interval_hours"] if setting else 24
            enabled = bool(setting and setting["enabled"])
            conn.execute(
                """INSERT INTO dream_projects(repo_id, next_run, cursor) VALUES (?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET next_run=excluded.next_run,
                cursor=excluded.cursor, last_error=NULL""",
                (
                    repo_id,
                    (now + timedelta(hours=interval)).isoformat() if enabled else None,
                    report["next_cursor"],
                ),
            )
            conn.commit()
            return report

    def history(self, repo_id):
        with self.storage._get_db() as conn:
            self._project(conn, repo_id)
            runs = [
                json.loads(row["report"])
                for row in conn.execute(
                    "SELECT report FROM dream_runs WHERE repo_id = ? "
                    "ORDER BY created_at DESC LIMIT 20",
                    (repo_id,),
                )
            ]
            for run in runs:
                actions = {
                    row["id"]: row["status"]
                    for row in conn.execute(
                        "SELECT id, status FROM dream_actions WHERE run_id = ?", (run["id"],)
                    )
                }
                for item in run["proposals"]:
                    if item.get("action_id") in actions:
                        item["resolution"] = actions[item["action_id"]]
            return runs

    def review(self, repo_id, run_id, proposal_id, decision, *, actor_id):
        with self.storage._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._project(conn, repo_id)
            row = conn.execute(
                "SELECT report FROM dream_runs WHERE id = ? AND repo_id = ?", (run_id, repo_id)
            ).fetchone()
            if not row:
                raise ValueError("Dreaming run not found")
            report = json.loads(row["report"])
            item = next((p for p in report["proposals"] if p["id"] == proposal_id), None)
            if not item or item["resolution"] != "pending":
                raise ValueError("This proposal is no longer pending")
            if decision == "archive" and item["kind"] == "expired":
                item["action_id"] = journal.apply(
                    self.storage, conn, run_id, repo_id, item, actor_id
                )
                item["resolution"] = "applied"
            elif decision == "dismiss":
                conn.execute(
                    "INSERT OR IGNORE INTO dream_dismissals VALUES (?, ?, ?)",
                    (repo_id, item["id"], journal.signature(item)),
                )
                item["resolution"] = "dismissed"
            else:
                raise ValueError("Only expired notes can be archived from dreaming")
            conn.execute(
                "UPDATE dream_runs SET report = ? WHERE id = ?", (json.dumps(report), run_id)
            )
            conn.commit()
            return item

    def undo(self, repo_id, action_id, *, actor_id):
        with self.storage._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._project(conn, repo_id)
            action = conn.execute(
                "SELECT * FROM dream_actions WHERE id = ? AND repo_id = ?", (action_id, repo_id)
            ).fetchone()
            if not action:
                raise ValueError("Dreaming change not found")
            journal.undo(self.storage, conn, action, actor_id)
            conn.commit()
        return {"status": "undone"}
