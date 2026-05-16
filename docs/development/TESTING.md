# LLM Memory - Testing Guide

This document covers testing practices, patterns, and procedures for LLM Memory.

---

## Quick Start

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/core/test_memory.py

# Run with verbose output
pytest -v

# Run with asyncio support (for async tests)
pytest --asyncio-mode=auto

# Run with coverage
pytest --cov=llm_memory --cov-report=html
```

---

## Test Structure

### Test Organization

```
tests/
├── capture/                 # Conversation and source capture tests
├── cli/                     # CLI integration tests
├── core/                    # Memory, storage, conflict, and LLM core tests
├── interfaces/              # MCP and other protocol interface tests
├── scripts/                 # Utility script smoke tests
├── server/                  # API, auth, config, collaboration tests
└── conftest.py              # Shared fixtures
```

---

## Testing Practices

### Test Isolation

Each test creates an isolated environment using temporary directories:

```python
import tempfile
from pathlib import Path

@pytest.fixture
def temp_memory():
    """Create isolated memory instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(
            storage=StorageConfig(
                data_dir=tmpdir,
                backend="local"
            )
        )
        memory = Memory(config)
        yield memory
```

**Key Principles:**
- Use `tempfile.TemporaryDirectory()` for isolated test storage
- Each test creates fresh `MemoryConfig` with temporary `data_dir`
- No shared state between tests
- Cleanup happens automatically

---

### Fixtures

Shared fixtures are defined in `conftest.py`:

```python
@pytest.fixture
def memory():
    """Reusable memory instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig(storage=StorageConfig(data_dir=tmpdir))
        yield Memory(config)

@pytest.fixture
def sample_memory_data():
    """Sample memory for testing."""
    return {
        "content": "Test memory",
        "importance": 0.5,
        "metadata": {"source": "test"}
    }
```

**Common Fixtures:**
- `memory` - Basic memory instance
- `neo4j_memory` - Memory with Neo4j backend (requires Neo4j running)
- `sample_memory_data` - Test data
- `mock_embeddings` - Mock embedding function

---

### Async Tests

For async code, use `pytest-asyncio`:

```python
import pytest

@pytest.mark.asyncio
async def test_async_operation(memory):
    result = await memory.async_recall("query")
    assert result is not None
```

Run with:
```bash
pytest --asyncio-mode=auto
```

---

## Test Patterns

### Testing Memory Operations

```python
def test_record_and_recall(memory):
    # Record a memory
    memory_id = memory.record("Test event")

    # Verify it was stored
    assert memory_id is not None

    # Recall it
    results = memory.recall("test")

    # Verify results
    assert len(results) > 0
    assert "test" in results[0]["content"].lower()
```

### Testing Storage Backends

```python
def test_storage_backend(storage):
    # Store
    memory_data = {
        "content": "Test",
        "importance": 0.5,
        "embedding": [0.1, 0.2, 0.3]
    }
    memory_id = storage.store_memory("episodic", memory_data)

    # Retrieve
    retrieved = storage.get_memory("episodic", memory_id)

    # Verify
    assert retrieved["content"] == "Test"
    assert retrieved["importance"] == 0.5
```

### Testing Search

```python
def test_semantic_search(memory):
    # Seed with memories
    memory.record("Python is a programming language")
    memory.record("JavaScript is used for web development")
    memory.record("Rust is a systems language")

    # Search
    results = memory.recall("programming languages")

    # Verify ranking
    assert len(results) > 0
    # Python should rank higher than JavaScript for this query
    contents = [r["content"] for r in results]
    assert contents[0].startswith("Python") or contents[0].startswith("Rust")
```

### Testing Compression

```python
def test_compression(memory):
    # Create multiple similar episodes
    for i in range(5):
        memory.record(f"Fixed authentication bug variant {i}")

    # Run compression
    compressed = memory.compress()

    # Verify semantic memory created
    assert len(compressed) > 0
    semantic = compressed[0]
    assert "authentication" in semantic["content"].lower()
    assert len(semantic.get("source_ids", [])) >= 2
```

---

## Manual CLI Testing

Test the system with real commands to verify end-to-end functionality:

### Basic Operations

```bash
# Initialize
llm-memory init --type code

# Record memories
llm-memory record "Implemented feature X"
llm-memory decision "Use PostgreSQL" "Need ACID guarantees"
llm-memory warn "src/auth.py" "Watch out for race conditions"

# Search and retrieve
llm-memory recall "feature"
llm-memory recall "PostgreSQL"

# Proactive context
llm-memory inject --file "src/auth.py"
llm-memory inject --task "Working on authentication"

# Error matching
llm-memory find-error "AttributeError"
```

### Maintenance Commands

```bash
# View status
llm-memory status
llm-memory stats

# List memories
llm-memory list --limit 10
llm-memory list --layer episodic

# Compression
llm-memory compress

# Memory decay
llm-memory decay

# Deduplication
llm-memory dedup --layer episodic
```

### Context Generation

```bash
# Full context for LLM
llm-memory context

# Context for specific file
llm-memory inject --file "src/module.py"

# Context for task
llm-memory inject --task "Refactoring database layer"
```

---

## Testing Different Backends

### Local Storage (SQLite + ChromaDB)

```bash
export LLM_MEMORY_STORAGE_BACKEND=local
pytest tests/core
```

### Neo4j Storage

```bash
# Requires Neo4j running
docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/testpass neo4j:5.15

export LLM_MEMORY_STORAGE_BACKEND=neo4j
export NEO4J_PASSWORD=testpass
pytest tests/core
```

### Remote Storage

```bash
# Start server
llm-memory serve --port 8000 &

export LLM_MEMORY_STORAGE_BACKEND=remote
export LLM_MEMORY_SERVER_URL=http://localhost:8000
pytest tests/server
```

---

## Integration Tests

### Server and Dashboard

```bash
# Start server
uvicorn llm_memory.server.app:app --port 8000

# Test API endpoints
curl http://localhost:8000/memories
curl http://localhost:8000/stats

# Test dashboard
curl http://localhost:8000/dashboard
# Should return HTML

# Visit in browser
open http://localhost:8000/dashboard
```

### MCP Server

```bash
# Test MCP server
llm-memory-mcp --help

# Test with Claude Desktop
# 1. Configure in claude_desktop_config.json
# 2. Restart Claude Desktop
# 3. Use memory tools in conversation
```

---

## Test Data

### Sample Memories

```python
SAMPLE_EPISODIC = [
    {
        "content": "Fixed authentication bug in login handler",
        "importance": 0.7,
        "category": "bug"
    },
    {
        "content": "Decided to use JWT tokens for stateless auth",
        "importance": 0.8,
        "category": "decision"
    }
]

SAMPLE_SEMANTIC = [
    {
        "content": "Auth module has race condition - use mutex",
        "importance": 0.9,
        "category": "warning"
    },
    {
        "content": "Always validate JWT signatures",
        "importance": 0.8,
        "category": "invariant"
    }
]
```

---

## Debugging Tests

### Verbose Output

```bash
pytest -v -s tests/core/test_memory.py
```

### Specific Test

```bash
pytest tests/core/test_memory.py::TestSearch::test_recall_finds_memories -v
```

### Failed Tests Only

```bash
pytest --lf  # Last failed
pytest --ff  # Failed first
```

### Debug with pdb

```bash
pytest --pdb  # Drop into debugger on failure
```

---

## Coverage

### Generate Coverage Report

```bash
# HTML report
pytest --cov=llm_memory --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=llm_memory --cov-report=term

# XML report (for CI)
pytest --cov=llm_memory --cov-report=xml
```

### Coverage Goals

- **Core modules**: >90% coverage
- **Layers**: >85% coverage
- **Interfaces**: >80% coverage
- **Overall**: >85% coverage

---

## Continuous Integration

### GitHub Actions

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      neo4j:
        image: neo4j:5.15
        env:
          NEO4J_AUTH: neo4j/testpass
        ports:
          - 7687:7687

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -e ".[dev]"

      - name: Run tests
        run: |
          pytest --cov=llm_memory --cov-report=xml
        env:
          NEO4J_PASSWORD: testpass

      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## Performance Testing

Use the supported benchmark script for performance checks instead of embedding
timing assertions in unit tests:

```bash
python scripts/benchmark_memory.py --items 100 --json
python scripts/benchmark_memory.py --items 1000
```

The benchmark defaults to SQLite storage and noop embeddings so it runs without
network downloads or model initialization. To benchmark Neo4j, start Neo4j first
and provide credentials:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=testpass
python scripts/benchmark_memory.py --backend neo4j --items 1000
```

---

## Common Issues

### Neo4j Connection Errors

```python
# Mock Neo4j for tests that don't need real database
@pytest.fixture
def mock_neo4j():
    with patch('neo4j.GraphDatabase.driver') as mock:
        yield mock
```

### Embedding Generation Slow

```python
# Use mock embeddings for faster tests
@pytest.fixture
def mock_embeddings():
    def fake_embed(texts):
        return [[0.1] * 384 for _ in texts]
    return fake_embed
```

### Temporary Directory Cleanup

```python
# Always use context manager
with tempfile.TemporaryDirectory() as tmpdir:
    # Test code here
    pass
# Cleanup happens automatically
```

---

## Test Checklist

Before submitting changes:

- [ ] All tests pass: `pytest`
- [ ] Code is linted: `ruff check .`
- [ ] Code is formatted: `ruff format .`
- [ ] Coverage >85%: `pytest --cov=llm_memory`
- [ ] Manual CLI testing completed
- [ ] New features have tests
- [ ] Edge cases covered
- [ ] Documentation updated

---

## Future Testing Improvements

- [ ] Property-based testing with Hypothesis
- [ ] Mutation testing with mutmut
- [ ] End-to-end browser tests for dashboard
- [ ] Load testing with locust
- [ ] Security testing with bandit
- [ ] Fuzz testing for input validation
