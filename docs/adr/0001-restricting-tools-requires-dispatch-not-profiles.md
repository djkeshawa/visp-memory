# ADR 0001: Restricting Tools Requires a Dispatch Check, Not a Profile

- **Status:** Accepted
- **Date:** 2026-08-01
- **Accepted:** 2026-08-02 under D-116
- **Workspace decision:** D-106

## Context

Workspace decision D-106 adopts a single verb vocabulary in which `visp recall`
and `visp learn` reach this project. Registering this MCP server beside those
verbs hands a coding agent a durable write path.

That matters more here than for a normal tool. The workspace's memory invariant
is *"retrieved memory is context, not permission."* It has a read half, which
this project already honours through provenance, lifecycle states and quarantine.
Its **write half has never been decided**: nothing today requires a durable
memory write to pass through review or human approval.

The surface plan initially proposed "register a read-only profile." Two facts
make that impossible as stated, and both are already visible in this repository:

1. **There is no read-only profile.** `VALID_MCP_PROFILES` at
   `src/visp_memory/interfaces/mcp.py:125` is exactly `{"core", "full"}`, selected
   by `VISP_MEMORY_MCP_PROFILE` and defaulting to `core`.
2. **Profiles cannot restrict anything.** The filter runs in `list_tools`;
   `call_tool` dispatches by name with no profile check. The module says so
   plainly at `:89` — *"Hidden tools remain fully functional if a client calls
   them by name; the profile only controls what is advertised."*

So a profile is an advertising decision, not an access-control one. Shipping one
as though it restricted writes would be the worst outcome available: a control
that reads as enforcement and enforces nothing.

The counts also matter. `core` advertises seventeen tools and **ten of them
write** — `memory_record`, `memory_decision`, `memory_learn`, `memory_warn`,
`memory_goal`, `memory_working_on`, `memory_done`, `memory_feedback_log`, plus
`memory_session_start`, which writes whenever a task argument is supplied. That is
not a narrow surface.

## Decision

**Restriction is enforced at dispatch, never by a profile.**

1. Add a restricted mode that is checked inside `call_tool`, so a tool outside it
   is refused **when called by name**, not merely hidden from `list_tools`.
2. A refusal is explicit and names the reason. It never returns an empty success,
   and never silently no-ops — a write that appears to succeed and did not is
   worse than a refusal.
3. Profiles keep their existing meaning: they control advertisement, for token
   efficiency. This ADR does not redefine them, and the comment at `:89` stays
   true.
4. The restricted mode is what the unified surface registers at alpha. Durable
   writes reach this project through a reviewed path rather than directly from a
   model's tool call.
5. **This project remains authoritative for how durable memory is stored and
   governed.** The restriction is about who may write unreviewed, not about
   relocating memory semantics. Nothing here grants workflow authority, and
   retrieved memory remains context rather than permission.

## Consequences

- The write half of the memory invariant is decided instead of left open, and is
  decided in the direction that can be relaxed later. Opening a restricted mode
  once a reviewed path is proven is safe; retracting an unreviewed write path
  after agents depend on it is not.
- **A reviewed write path does not exist yet and must be built.**
  `visp-hyper remember` calls the provider directly today, so "route `learn`
  through review" describes something that has to be written.
- **The consuming side cannot reach this project at all yet.** Hyper's entire
  dependency set is `{commander, zod}` — no MCP client. Either three
  `visp-memory` CLI commands gain `--json` and Hyper shells out, or Hyper gains an
  MCP client. The CLI route is smaller, and whether adding `--json` counts as
  publishing a new surface under the export gates (`NO-GO` under D-067) must be
  confirmed rather than assumed.
- Standalone users are unaffected. `full` and `core` keep working exactly as they
  do now; the restricted mode is opt-in and exists for the composed surface.
- `visp-memory hooks install` stays outside the supported install path at alpha.
  It writes `AGENTS.md`, `.claude/settings.json`, `CLAUDE.md`, `.cursorrules` and
  a **global** `~/.codex/config.toml` whose single table means a second project
  silently repoints the first. It remains available to standalone users, with
  `doctor` warning when two writers have touched one file.

## Alternatives considered

**Register `core` as-is and rely on instructions.** Rejected. The instruction
layer is advisory and the tools stay callable by name; this is the option that
looks like a control and is not one.

**Register nothing at alpha.** Safe, and drops the memory half of the goal
entirely. Rejected as a first choice, but it remains the correct fallback if the
dispatch change or the transport cannot land in time — `recall` and `learn`
refusing cleanly is honest, where a half-wired write path is not.
