# LLM Memory Enhancement Implementation Plan

## Status: Phase 1.1 In Progress

Last updated: 2026-01-03

---

## Overview

Transform llm-memory from manual note-taking into an **active memory system** with:
- ✅ Automatic capture from git/tests/conversations
- ✅ Proactive memory injection without being asked
- ✅ Pattern detection across sessions
- ✅ Memory quality management (dedup, conflicts, confidence)
- ✅ Smart hierarchical compression
- ✅ Feedback-driven learning
- ⏳ Multi-agent sync (future)

**Design Principles:**
- LLM-agnostic (works with Claude Code, Cursor, Aider, Continue, custom agents)
- Multiple integration methods (MCP, context files, CLI, Python SDK)
- Graceful degradation (optional dependencies)
- Provider-agnostic (OpenAI, Anthropic, Ollama, local models)

---

## Implementation Phases

### Phase 1: Automatic Capture ⏳

#### 1.1 Git Capture (IN PROGRESS)
**Status:** Creating git.py module

**Files:**
- ✅ `src/llm_memory/capture/__init__.py`
- ⏳ `src/llm_memory/capture/git.py` - GitCapture class
- ⏳ `src/llm_memory/interfaces/cli.py` - Add capture commands
- ⏳ `src/llm_memory/config.py` - Add capture config section
- ⏳ `pyproject.toml` - Add gitpython dependency

**Features:**
- Auto-record commits via post-commit hook
- Auto-record merges via post-merge hook
- Parse conventional commits
- Analyze diffs to infer intent
- CLI: `llm-memory capture git install/uninstall/sync`

#### 1.2 Test Capture (PENDING)
**Files:**
- `src/llm_memory/capture/tests.py` - TestCapture class

**Features:**
- Parse pytest output
- Record test failures as bug_found
- Detect flaky tests → fragile_area warnings
- Pytest plugin integration

#### 1.3 Conversation Capture (PENDING)
**Files:**
- `src/llm_memory/capture/conversation.py` - ConversationCapture class

**Features:**
- Extract learnings from LLM conversations
- Detect corrections (LLM was wrong)
- Detect decisions discussed
- Extract file-specific knowledge

---

### Phase 2: Proactive Recall (PENDING)

#### 2.1 File-Triggered Recall
**Files:**
- `src/llm_memory/recall/__init__.py`
- `src/llm_memory/recall/proactive.py` - ProactiveRecall class

**Features:**
- on_file_open() → Surface warnings, bugs, patterns
- on_error() → Find similar past errors
- on_directory() → Aggregate knowledge
- Format for LLM context injection

#### 2.2 Universal Tool Adapters
**Files:**
- `src/llm_memory/hooks/__init__.py`
- `src/llm_memory/hooks/base.py` - LLMToolAdapter ABC
- `src/llm_memory/hooks/claude_code.py` - Claude Code adapter
- `src/llm_memory/hooks/cursor.py` - Cursor adapter (.cursorrules)
- `src/llm_memory/hooks/aider.py` - Aider adapter
- `src/llm_memory/hooks/generic.py` - Generic file-based adapter

**Integration Methods:**
1. MCP Server (Claude Desktop, Claude Code)
2. Context files (CLAUDE.md, .cursorrules, .aider)
3. Shell hooks
4. Python SDK

**CLI:**
- `llm-memory hooks install <tool>` - Install for specific tool
- `llm-memory hooks list` - Show available integrations
- `llm-memory inject --files FILE...` - Manual injection

#### 2.3 MCP Enhancements
**Files:**
- `src/llm_memory/interfaces/mcp.py` (modify)

**New Resources:**
- `memory://file/{path}` - Memories for specific file
- `memory://error/{hash}` - Similar past errors
- `memory://session` - Current session context

---

### Phase 3: Pattern Detection (PENDING)

#### 3.1 Pattern Detector
**Files:**
- `src/llm_memory/analysis/__init__.py`
- `src/llm_memory/analysis/patterns.py` - PatternDetector class

**Features:**
- detect_repeated_bugs(threshold=3)
- detect_hotspot_files()
- detect_recurring_decisions()
- detect_correction_patterns()

#### 3.2 Knowledge Extractor
**Files:**
- `src/llm_memory/analysis/extractor.py` - KnowledgeExtractor class

**Features:**
- bugs_to_warning() → Multiple bugs → fragile_area
- decisions_to_convention() → Recurring factors → convention
- corrections_to_learning() → Repeated fixes → invariant

#### 3.3 Background Analysis
**Files:**
- `src/llm_memory/core/memory.py` (modify - add analyze())

**CLI:**
- `llm-memory analyze` - Run analysis
- `llm-memory analyze --watch` - Continuous background

---

### Phase 4: Quality Management (PENDING)

#### 4.1 Deduplication
**Files:**
- `src/llm_memory/quality/__init__.py`
- `src/llm_memory/quality/dedup.py` - Deduplicator class
- `src/llm_memory/core/storage.py` (modify - add dedup check)

**Features:**
- is_duplicate(content, threshold=0.9)
- find_duplicates()
- merge_duplicates(ids)

#### 4.2 Conflict Resolution
**Files:**
- `src/llm_memory/quality/conflicts.py` - ConflictResolver class

**Features:**
- detect_conflicts() → Find contradictory memories
- resolve_by_recency()
- flag_for_review()

#### 4.3 Confidence Scoring
**Files:**
- `src/llm_memory/core/storage.py` (modify - add confidence field)
- `src/llm_memory/quality/confidence.py` - ConfidenceScorer class

**Schema Change:**
```sql
ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 0.5;
```

**Features:**
- memory_helped(id) → Increase confidence
- memory_wrong(id) → Decrease confidence
- memory_irrelevant(id) → Slight decrease

---

### Phase 5: Smarter Compression (PENDING)

#### 5.1 Hierarchical Compression
**Files:**
- `src/llm_memory/core/compression.py` (enhance)

**Features:**
- compress_level_1() → Detailed summary (gist)
- compress_level_2() → Extract pattern (rule)
- compress_level_3() → High-level insight (principle)

#### 5.2 Context-Aware Compression
**Enhanced LLM Prompt:**
Extract:
1. What triggered these events
2. What the outcomes were
3. What generalizable lesson applies
4. What conditions make this relevant

#### 5.3 Compression Scheduling
**Files:**
- `src/llm_memory/core/scheduler.py` - CompressionScheduler class

**Features:**
- schedule_compression()
- should_compress(layer, age, count)
- run_scheduled()

---

### Phase 6: Feedback Loop (PENDING)

#### 6.1 Feedback Collection
**Files:**
- `src/llm_memory/feedback/__init__.py`
- `src/llm_memory/feedback/collector.py` - FeedbackCollector class

**MCP Tools:**
- `memory_feedback_helpful`
- `memory_feedback_wrong`
- `memory_feedback_irrelevant`

#### 6.2 Validation Pipeline
**Files:**
- `src/llm_memory/feedback/validator.py` - MemoryValidator class

**Features:**
- validate_file_references() → Check files exist
- validate_function_references() → Check functions exist
- flag_stale()
- auto_deprecate(age_days)

#### 6.3 Learning from Feedback
**Files:**
- `src/llm_memory/core/compression.py` (modify)

**Features:**
- Weight compression by feedback scores
- Exclude low-confidence from semantic extraction
- Promote high-feedback to higher importance

---

### Phase 7: Multi-Agent Support (FUTURE)

#### 7.1 Memory Attribution
**Files:**
- `src/llm_memory/core/storage.py` (modify)

**Schema Changes:**
```sql
ALTER TABLE memories ADD COLUMN author TEXT;
ALTER TABLE memories ADD COLUMN author_type TEXT; -- 'human', 'llm', 'system'
```

#### 7.2 Sync Protocol
**Files:**
- `src/llm_memory/sync/__init__.py`
- `src/llm_memory/sync/protocol.py` - MemorySync class

**Features:**
- export_delta(since)
- import_delta(changes)
- resolve_merge_conflicts()

---

## Configuration Schema

New sections to add to `llm-memory.yaml`:

```yaml
capture:
  git:
    enabled: true
    auto_install_hooks: true
    parse_conventional_commits: true
  tests:
    enabled: true
    pytest_plugin: true
  conversation:
    enabled: false  # Requires explicit opt-in

recall:
  proactive: true
  file_triggered: true
  error_matching: true

analysis:
  pattern_detection: true
  auto_extract: true
  run_interval_hours: 24

quality:
  deduplication: true
  similarity_threshold: 0.9
  conflict_detection: true

feedback:
  collection: true
  auto_validate: true
  validate_interval_hours: 168  # Weekly
```

---

## Dependencies

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
capture = [
    "gitpython>=3.1",
    "watchdog>=3.0",
]
analysis = [
    "scikit-learn>=1.0",  # For clustering similar memories
]
```

---

## Success Metrics

1. **Automatic capture rate:** >80% of git commits auto-recorded
2. **Proactive recall precision:** >70% of surfaced memories are relevant
3. **Duplicate prevention:** <5% duplicate memories
4. **Feedback incorporation:** Confidence scores correlate with usefulness
5. **Pattern detection:** Identifies recurring issues within 3 occurrences

---

## Progress Tracking

Use llm-memory itself to track progress:

```bash
# Load context for next session
llm-memory context

# Record when completing a phase
llm-memory record "Completed Phase 1.1: Git capture module" -c feature_added

# Update current work
llm-memory working "Phase X.Y: Description" --file path/to/file.py
llm-memory done  # When finished

# View implementation order
llm-memory recall "implementation order"

# Check what's left
llm-memory list-intents
```

---

## Next Steps

**Currently:** Phase 1.1 - Git Capture Module

1. ✅ Create capture/__init__.py
2. ⏳ Create capture/git.py with GitCapture class
3. ⏳ Add capture commands to cli.py
4. ⏳ Update config.py with capture settings
5. ⏳ Update pyproject.toml with dependencies
6. ⏳ Test git hook installation
7. ⏳ Document usage in README

**Then:** Phase 2.1 - Proactive Recall
