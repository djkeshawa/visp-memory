# Bug Fixes - 2026-01-03

## Summary
Fixed 4 critical bugs that were preventing core functionality from working properly.

## Bugs Fixed

### 1. Vector Embeddings Not Functioning ✅

**Issue**: Neo4jStorage wasn't generating or using vector embeddings for semantic search, always falling back to simple text filtering.

**Root Cause**:
- Neo4jStorage didn't accept or use an `embedding_fn` parameter
- Memory class was using wrong config attribute name (`embeddings` instead of `embedding`)

**Files Changed**:
- `src/llm_memory/core/neo4j_storage.py`
  - Added `embedding_fn` parameter to `__init__` (line 23)
  - Auto-generate embeddings in `store_memory()` if not provided (lines 103-109)
  - Auto-generate embeddings in `search_memories()` for queries (lines 195-201)
- `src/llm_memory/core/memory.py`
  - Initialize embedding provider before storage backends (lines 71-80)
  - Fixed config attribute: `config.embedding` not `config.embeddings` (line 75)
  - Pass `embedding_fn` to all storage backends (lines 92, 95)

**Testing**:
```bash
llm-memory record "Test with embeddings"
llm-memory recall "semantic search"
# Now shows similarity scores (e.g., 0.64) instead of 0.0
```

### 2. `llm-memory decision` Parameter Issue ✅

**Issue**: CLI command failed with TypeError when using `--repo` flag

**Root Cause**: Memory.decision() didn't accept `repo_id` parameter but CLI was passing it

**Files Changed**:
- `src/llm_memory/core/memory.py`
  - Added `repo_id` parameter to `decision()` method (line 160)
  - Use provided `repo_id` or fall back to `config.repo_id` (line 178)

**Testing**:
```bash
llm-memory decision "Use PostgreSQL" "Need ACID" --repo my-project
# Works without error
```

### 3. `llm-memory inject` AttributeError ✅

**Issue**: Command crashed with `AttributeError: 'str' object has no attribute 'value'`

**Root Cause**:
- `SemanticMemory.search()` assumed category was always an Enum and called `.value` on strings
- `EpisodicMemory.search()` had the same issue

**Files Changed**:
- `src/llm_memory/layers/semantic.py`
  - Added isinstance check to handle both Enum and string category (lines 241-247)
- `src/llm_memory/layers/episodic.py`
  - Added isinstance check to handle both Enum and string category (lines 230-236)

**Testing**:
```bash
llm-memory inject --file "src/module.py"
llm-memory inject --task "Working on feature X"
# Both work without AttributeError
```

### 4. Category String/Enum Handling in Proactive Recall ✅

**Issue**: ProactiveRecall.find_relevant_for_task() passed string category "pattern" to semantic.search(), triggering the AttributeError

**Root Cause**: Internal code was mixing string and Enum category values

**Files Changed**:
- Same fix as #3 - the isinstance check handles internal calls too

**Testing**:
```bash
llm-memory inject --task "Implementing new feature"
# Now retrieves relevant patterns, knowledge, and warnings
```

## Verification

All fixes tested with real commands:
```bash
# Vector embeddings working
llm-memory recall "vector search"
# Output shows similarity scores: 0.64, 0.52, etc.

# Decision with repo_id
llm-memory decision "Fixed bugs" "Improve usability" --repo llm-memory

# Inject working
llm-memory inject --file "src/llm_memory/core/neo4j_storage.py"
# Output:
⚠️  Warnings
  • Vector index requires Neo4j 5.15+ with vector index support enabled
💡 Relevant Knowledge
  • Always initialize embedding_fn before passing to storage backends

# Error finding works
llm-memory find-error "AttributeError"
# Returns similar past errors with fixes
```

## Impact

These fixes restore critical functionality:
- **Semantic search** now works properly with vector similarity
- **All CLI commands** execute without errors
- **Proactive context injection** (`inject` command) provides relevant warnings and knowledge
- **Error history search** helps debug recurring issues

The system is now fully functional for daily use.
