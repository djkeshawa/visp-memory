# The machine contract surface

`visp-memory contract ...` is the versioned surface a coordinator speaks. Every
command in it prints one JSON object on stdout, always carrying `contractVersion`
and `success`, and reports failure as a `success: false` envelope rather than a
traceback.

This document exists because the surface had no documentation at all while the rest
of the CLI had plenty — which is how it came to advertise a capability it did not
have and hide two it did. The second half of the page, **what is not on the
contract**, is the more useful half: it is the list a coordinator author needs
before they design around an assumption that is not true.

## What is on the contract

| Command | Returns | Notes |
|---|---|---|
| `contract recall QUERY` | Ranked `entries` with scores | Accepts `--task`, `--file`, `--symbol`, `--constraint`, `--session`, `--environment`, `--task-type`, `--as-of`, `--min-score`, `--repo`. Naming files or symbols can only *add* structurally-reached memories, never remove or reorder the text results, and adds a `retrieval` block describing what the structure channel did — including when it did nothing, and why. With no files or symbols the envelope is byte-identical to the pre-0.5 one, so an un-upgraded coordinator sees no change. |
| `contract propose CONTENT` | `proposalId`, `status: quarantined`, `durable: false`, and the exact `acceptCommand` / `rejectCommand` | A proposal is **not** retrievable by `contract recall` until a human accepts it. |

`--endpoint` is accepted on both and ignored: it exists so a coordinator's argument
list stays stable. Nothing on this surface opens a network connection.

## What is not on the contract

Everything below is implemented, tested, and reachable only by shelling out to a
human-facing command whose output is Rich-formatted text, not JSON. Ranked by how
much the gap costs the correctness of code built with this package.

### 1. The task brief — `visp-memory brief`

The flagship retrieval path. `brief` returns cited, token-budgeted context that
separates warnings from decisions from knowledge, names the active intent and its
constraints, and reports contradictions and unknowns explicitly. `contract recall`
returns a flat ranked list with none of that structure.

A coordinator assembling a prompt therefore gets the weakest of the three retrieval
paths this package offers, and has to re-derive "is this a warning I must not
violate, or a note" from prose. `brief` already emits JSON via `--format json`; it
is the single largest and cheapest gap on this list.

### 2. Closing the proposal loop — `visp-memory review list|accept|reject`

`contract propose` now correctly names these commands, so the loop is at least
*possible*. But the second half of it leaves the contract: `review list` prints a
table for a human, so a coordinator that wants to know what is pending must parse
formatted text, and `review accept` prints a sentence rather than an envelope. Half
a lifecycle on a machine surface is an asymmetry an integrator will feel
immediately.

### 3. Recall feedback — `visp-memory feedback log`

Reinforcement is "use it or lose it": a memory that is recalled and used gains
strength, and one that is never used decays out. The MCP `core` profile includes
`memory_feedback_log` for exactly this reason — without it a client can only emit
non-reinforcing events.

The contract has no equivalent. **Every memory a coordinator recalls and acts on
decays as though it had never been used.** A long-running coordinator is therefore
actively eroding the store it depends on, silently. This is the gap most likely to
degrade quality over time rather than at a single call.

### 4. Explaining a decision — `visp-memory preview`

Shows what would be injected for a task, what was dropped, and why — quarantined,
below the trust threshold, below the relevance floor, redundant, over budget. When
a coordinator gets nothing back from `contract recall`, it currently cannot tell
"the store has nothing" from "five memories were withheld as untrusted", and those
call for opposite responses.

### 5. Error precedent — `visp-memory find-error`

"Have we hit this exception before, and what fixed it" is a direct correctness
question with a direct answer in the store. A coordinator handling a failing build
has no contract route to it and would have to phrase the stack trace as a recall
query, which is precisely the text-similarity path this feature exists to avoid.

### 6. Provenance and eligibility — `visp-memory audit`

Where a memory came from, whether it is superseded, whether it is injectable at all.
A coordinator that wants to weight or disclose its evidence cannot see any of it.

### 7. Conflict checking — `visp-memory quality conflicts`

Whether a statement the coordinator is about to record contradicts existing
knowledge. Cheap to expose, and the natural companion to `contract propose`.

## Deliberately not on the contract

- **Direct writes** (`record`, `decision`, `learn`, `warn`). A coordinator proposes;
  a human accepts. `contract propose` is the only write, and it quarantines. This is
  the design, not an oversight.
- **Maintenance** (`decay`, `compress`, `dedup`, `import`, `export`, `serve`, `init`,
  `hooks install`). Operator actions, not agent actions.
- **Frozen areas** (`teams`, `admin`, `repos`) — see
  [FEATURE_STATUS.md](../FEATURE_STATUS.md).

## Rule for anything added here

A contract command that reports a capability the package does not have is worse than
a missing command, because a caller builds a pipeline on it. `contract propose` told
integrators "No CLI command currently accepts a proposal" for as long as `review
accept` existed and worked. The test that fixed it does not assert the wording — it
reads the note, runs the command the note names, and asks recall whether the promise
was kept. Hold new commands to that: pin the promise by executing it.
