"""Project-scoped dreaming: deterministic proposals and recoverable exact cleanup."""

import json
import secrets
from datetime import timedelta

from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.dreaming import journal
from visp_memory.core.dreaming.persistence import defaults, transaction
from visp_memory.core.dreaming.planner import SCAN_LIMIT, plan
from visp_memory.core.storage import LocalStorage


class Dreaming:
    def __init__(self, storage):
        from visp_memory.core.neo4j_storage import Neo4jStorage

        if not isinstance(storage, (LocalStorage, Neo4jStorage)):
            raise NotImplementedError("Dreaming requires SQLite or Neo4j storage")
        self.storage = storage
        if isinstance(storage, LocalStorage):
            with storage._get_db() as conn:
                conn.executescript(journal.SCHEMA)
                conn.commit()

    def _project(self, unit, repo_id):
        repo = unit.project(repo_id)
        if not repo or repo.get("status", "active") != "active":
            raise ValueError("Select an active, registered project")
        return repo

    @staticmethod
    def _settings(unit, repo_id):
        rows = unit.rows("projects", repo_id=repo_id)
        result = {**defaults(repo_id), **(rows[0] if rows else {})}
        result["enabled"] = bool(result["enabled"])
        return result

    def settings(self, repo_id):
        with transaction(self.storage) as unit:
            self._project(unit, repo_id)
            return self._settings(unit, repo_id)

    def configure(self, repo_id, *, enabled, interval_hours=24):
        if not isinstance(enabled, bool) or interval_hours not in {6, 12, 24, 168}:
            raise ValueError("Choose an interval of 6, 12, 24, or 168 hours")
        with transaction(self.storage, write=True) as unit:
            self._project(unit, repo_id)
            setting = self._settings(unit, repo_id)
            setting.update(
                enabled=enabled,
                interval_hours=interval_hours,
                last_error=None,
                next_run=(utc_now() + timedelta(hours=interval_hours)).isoformat()
                if enabled
                else None,
            )
            unit.put("projects", setting)
            return setting

    def _plan(self, unit, repo_id, now):
        self._project(unit, repo_id)
        cursor = self._settings(unit, repo_id)["cursor"]
        rows = unit.memories(repo_id, cursor, SCAN_LIMIT + 1)
        memories = rows[:SCAN_LIMIT]
        linked = unit.linked_ids([m["id"] for m in memories if m["layer"] == "episodic"])
        report = plan(memories, linked, now)
        dismissed = {
            (r["proposal_id"], r["signature"]) for r in unit.rows("dismissals", repo_id=repo_id)
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
        with transaction(self.storage) as unit:
            return self._plan(unit, repo_id, utc_now())

    def run(self, repo_id, *, actor_id, scheduled=False):
        with transaction(self.storage, write=True) as unit:
            now = utc_now()
            setting = self._settings(unit, repo_id)
            if scheduled and (
                not setting["enabled"] or (parse_utc(setting["next_run"]) or now) > now
            ):
                return None
            report = self._plan(unit, repo_id, now)
            run_id = "drm_" + secrets.token_hex(10)
            for item in report["proposals"]:
                if item["kind"] == "duplicate" and item["automatic"]:
                    item["action_id"] = journal.apply(unit, run_id, repo_id, item, actor_id)
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
            unit.put(
                "runs",
                dict(
                    id=run_id,
                    repo_id=repo_id,
                    created_at=now.isoformat(),
                    report=json.dumps(report),
                ),
            )
            setting.update(
                next_run=(now + timedelta(hours=setting["interval_hours"])).isoformat()
                if setting["enabled"]
                else None,
                cursor=report["next_cursor"],
                last_error=None,
            )
            unit.put("projects", setting)
            return report

    def history(self, repo_id):
        with transaction(self.storage) as unit:
            self._project(unit, repo_id)
            rows = unit.rows("runs", repo_id=repo_id, newest=True, limit=20)
            runs = [json.loads(row["report"]) for row in rows]
            for run in runs:
                actions = {
                    row["id"]: row["status"] for row in unit.rows("actions", run_id=run["id"])
                }
                for item in run["proposals"]:
                    if item.get("action_id") in actions:
                        item["resolution"] = actions[item["action_id"]]
            return runs

    def review(self, repo_id, run_id, proposal_id, decision, *, actor_id):
        with transaction(self.storage, write=True) as unit:
            self._project(unit, repo_id)
            rows = unit.rows("runs", id=run_id, repo_id=repo_id)
            if not rows:
                raise ValueError("Dreaming run not found")
            report = json.loads(rows[0]["report"])
            item = next((p for p in report["proposals"] if p["id"] == proposal_id), None)
            if not item or item["resolution"] != "pending":
                raise ValueError("This proposal is no longer pending")
            if decision == "archive" and item["kind"] == "expired":
                item["action_id"] = journal.apply(unit, run_id, repo_id, item, actor_id)
                item["resolution"] = "applied"
            elif decision == "dismiss":
                unit.put(
                    "dismissals",
                    dict(
                        repo_id=repo_id, proposal_id=item["id"], signature=journal.signature(item)
                    ),
                )
                item["resolution"] = "dismissed"
            else:
                raise ValueError("Only expired notes can be archived from dreaming")
            unit.put("runs", {**rows[0], "report": json.dumps(report)})
            return item

    def undo(self, repo_id, action_id, *, actor_id):
        with transaction(self.storage, write=True) as unit:
            self._project(unit, repo_id)
            actions = unit.rows("actions", id=action_id, repo_id=repo_id)
            if not actions:
                raise ValueError("Dreaming change not found")
            journal.undo(unit, actions[0], actor_id)
        return {"status": "undone"}

    def due_projects(self, now):
        with transaction(self.storage) as unit:
            rows = sorted(unit.rows("projects"), key=lambda r: r.get("next_run") or "")
            return [
                r["repo_id"]
                for r in rows
                if r.get("enabled")
                and r.get("next_run")
                and parse_utc(r["next_run"]) <= now
                and (unit.project(r["repo_id"]) or {}).get("status") == "active"
            ][:10]

    def record_failure(self, repo_id):
        with transaction(self.storage, write=True) as unit:
            settings = self._settings(unit, repo_id)
            settings.update(
                last_error="The last cycle failed; no partial cleanup was committed.",
                next_run=(utc_now() + timedelta(minutes=10)).isoformat(),
            )
            unit.put("projects", settings)
