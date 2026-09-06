# Dreaming cycles

Dreaming is a periodic memory-care feature for SQLite-backed servers. Open **Dreaming**
in the dashboard, select an active project, and use **Preview cycle** to inspect the next
batch. **Run dreaming now** applies eligible exact-duplicate merges and records review
suggestions. Schedule controls support every 6 hours, 12 hours, daily, or weekly.
Schedules are paused by default and are saved per project in its local memory database.

## What a cycle does

- **Exact duplicates:** only episodic notes with identical content, case, category,
  importance, tags, provenance, and metadata qualify for automatic merging. The most-used
  copy remains active. Other copies become recoverable `merged` records. Original
  content and Evidence are retained.
- **Related memories:** shared words generate source-linked draft excerpts for review.
  The draft can be copied; it is not automatically saved as semantic knowledge.
- **Possible contradictions:** differing negation signals in related text produce review
  suggestions. This heuristic does not establish truth or catch every contradiction.
- **Expired information:** a recorded `valid_to` in the past produces an archive
  suggestion. Archiving requires an explicit review action and is reversible.

Pinned, held, approved, warning/prohibition, derived, and graph-linked notes are protected
from automatic merging. Semantic knowledge is not automatically merged. Differences in
project, author/team, environment, task scope, or validity cannot be collapsed by the
exact-duplicate path. General source editing and semantic merges remain in Memories.

This first version uses exact content and lexical overlap. It works without Chroma,
embeddings, a paid provider, or an LLM. It neither calls an LLM nor changes intent status,
permissions, evidence authority, or workflow readiness. It does not purge memories or
apply the legacy compounding importance-decay routine.

## Scheduling and recovery

The server checks schedules once a minute. Due cycles wait for five minutes without
non-GET API activity outside dreaming controls. Health checks and dashboard polling do
not prevent a cycle. Activity is observed by each server process; this is an idle hint,
not a global lock across hosts or CLI processes. Explicit runs execute immediately.
The server must be running for schedules to execute; overdue work resumes after startup.
Archived projects are skipped.

SQLite write transactions serialize concurrent runs and record changes with their
history atomically. A crash or failed cycle leaves no partial cleanup committed. A failed
background cycle shows an error in its schedule and is retried after ten minutes.
No second scheduler service or Docker container is required.

Each cycle scans at most 500 active episodic/semantic memories, compares at most 5,000
candidate pairs, and returns at most 40 findings. Larger projects rotate through ID-ordered
batches. Cross-batch duplicates and relationships may need manual review. A limited batch
is labeled in the dashboard; an empty batch does not certify the whole store as clean.

The dashboard shows the most recent 20 runs. History and action journals stay in
`memories.db` and are included in full storage backups. **Undo change** restores affected
statuses and metadata. If a source was subsequently edited or purged, undo refuses to
overwrite that work. Undo and dismissal suppress the unchanged proposal in future cycles;
a material source change allows it to be reconsidered. Stored source excerpts are project
data and are visible only through the administrator-gated dreaming endpoints.

## API

All routes require an administrator with access to the selected active project.
Personal access tokens also need the `admin` scope. Browser mutations use the existing
session and CSRF protections. Unsupported backends return HTTP 501; stale review actions
return HTTP 409.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/dreaming/{repo_id}` | Schedule and recent run history |
| GET | `/dreaming/{repo_id}/preview` | Read-only memory preview |
| PUT | `/dreaming/{repo_id}/schedule` | `{ "enabled": true, "interval_hours": 24 }` |
| POST | `/dreaming/{repo_id}/run` | Run a cycle immediately |
| POST | `/dreaming/{repo_id}/runs/{run_id}/proposals/{proposal_id}` | `{ "decision": "archive" }` or `"dismiss"` |
| POST | `/dreaming/{repo_id}/actions/{action_id}/undo` | Undo an applied change |
