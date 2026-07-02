export const meta = {
  name: 'led-orchestration',
  description: 'Specialist muscle for a chat-led team: runs Sonnet-5-high search/extraction agents and Opus-4.8-high implementation agents. The LEADER is the chat agent (you), not an agent inside this script.',
  whenToUse: 'Call this when you (the chat, acting as leader) have a plan and want the specialists executed: pass research questions to fan out Sonnet-5 search agents, and/or implementation tasks to run Opus-4.8 agents. Review the raw results yourself and decide next steps.',
  phases: [
    { title: 'Gather', detail: 'Sonnet-5-high agents answer the leader\'s research questions in parallel', model: 'sonnet' },
    { title: 'Implement', detail: 'Opus-4.8-high agents execute the leader\'s implementation tasks', model: 'opus' },
  ],
}

// The LEADER is the chat agent. This script contains ZERO leader/review agents.
// It runs specialists and returns their raw output for the leader to judge.
const SEARCH = { model: 'sonnet', effort: 'high' } // read-only search + data extraction
const IMPL = { model: 'opus', effort: 'high' }     // implementation

// Accept: object args, JSON-stringified object args, or a bare-string question.
let raw = args
if (typeof raw === 'string') {
  const t = raw.trim()
  if (t.startsWith('{') || t.startsWith('[')) { try { raw = JSON.parse(t) } catch { /* keep as string */ } }
}
const cfg = (typeof raw === 'string') ? { questions: [{ id: 'q1', question: raw }] } : (raw || {})
const QUESTIONS = cfg.questions || []
const TASKS = cfg.tasks || []
const BRIEFING = cfg.briefing || '' // leader-approved context passed down to implementers
const GOAL = cfg.goal || ''
const SEQUENTIAL = cfg.sequential !== false // implementation defaults to sequential (shared tree)

if (!QUESTIONS.length && !TASKS.length) {
  throw new Error('led-orchestration: pass args.questions (to gather) and/or args.tasks (to implement)')
}

const RESEARCH_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['question_id', 'findings', 'confidence'],
  properties: {
    question_id: { type: 'string' },
    findings: { type: 'string', description: 'Extracted answer with concrete file:line references' },
    evidence: { type: 'array', items: { type: 'string' } },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
}

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['task_id', 'summary_of_changes'],
  properties: {
    task_id: { type: 'string' },
    summary_of_changes: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    self_check: { type: 'string', description: 'How the agent convinced itself the change is correct' },
    notes: { type: 'string' },
    blocked_on: { type: 'string', description: 'Set if the agent could not complete and needs leader direction' },
  },
}

const out = { gather: [], implement: [] }

// ---- GATHER: fan out Sonnet-5 search agents (read-only, parallel) ----------
if (QUESTIONS.length) {
  phase('Gather')
  const gathered = await parallel(QUESTIONS.map(q => () =>
    agent(
      `You are a SEARCH / DATA-EXTRACTION agent (Sonnet 5) working for a chat-based leader. Read-only: do NOT edit files. Answer precisely with concrete file:line evidence.

QUESTION [${q.id}]: ${q.question}${q.why ? `\nWHY IT MATTERS: ${q.why}` : ''}${GOAL ? `\n\nOverall goal for context: ${GOAL}` : ''}`,
      { ...SEARCH, phase: 'Gather', label: `search:${q.id}`, schema: RESEARCH_SCHEMA },
    )))
  out.gather = gathered.filter(Boolean)
  log(`Gathered ${out.gather.length}/${QUESTIONS.length} research answer(s) for the leader to review.`)
}

// ---- IMPLEMENT: run Opus-4.8 agents on the leader's tasks ------------------
if (TASKS.length) {
  phase('Implement')
  const runOne = (task) => agent(
    `You are an IMPLEMENTATION agent (Opus 4.8) working for a chat-based leader. Execute EXACTLY this task and nothing else. Follow the repo's existing conventions. Verify your change (build/tests/reasoning) before reporting. If you cannot complete it correctly, stop and set blocked_on with what you need — do not guess.

TASK [${task.id}] ${task.title || ''}
INSTRUCTIONS:
${task.instructions}
${task.files && task.files.length ? `EXPECTED FILES: ${task.files.join(', ')}\n` : ''}${BRIEFING ? `\nLEADER-APPROVED CONTEXT (rely on this; don't re-derive):\n${BRIEFING}\n` : ''}${task.feedback ? `\nREWORK — the leader rejected a prior attempt. Address precisely:\n${task.feedback}` : ''}`,
    { ...IMPL, phase: 'Implement', label: `impl:${task.id}`, schema: IMPL_SCHEMA },
  )

  if (SEQUENTIAL) {
    // shared working tree — serialize so accumulated edits stay consistent
    for (const task of TASKS) out.implement.push(await runOne(task))
  } else {
    // caller asserts tasks are file-disjoint; isolate to avoid write collisions
    out.implement = (await parallel(TASKS.map(t => () =>
      agent(
        `You are an IMPLEMENTATION agent (Opus 4.8). Execute EXACTLY this task in your isolated worktree.

TASK [${t.id}] ${t.title || ''}
INSTRUCTIONS:
${t.instructions}
${BRIEFING ? `\nLEADER-APPROVED CONTEXT:\n${BRIEFING}` : ''}`,
        { ...IMPL, phase: 'Implement', label: `impl:${t.id}`, schema: IMPL_SCHEMA, isolation: 'worktree' },
      )))).filter(Boolean)
  }
  log(`Implementation pass complete: ${out.implement.filter(Boolean).length}/${TASKS.length} task(s) reported back.`)
}

// Raw specialist output — the chat leader reviews, redirects, and decides next.
return out
