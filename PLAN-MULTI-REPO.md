# LLM Memory: Multi-Team, Multi-Repo MVP Plan

**Goal:** Validate if teams find cross-repo organizational memory useful before building everything.

**Timeline:** 2-3 weeks to testable MVP

**Success Criteria:** 3+ teams use it regularly for 2+ weeks and report it's more useful than Slack search + docs

---

## Current State → Target State

### Current (Solo-Dev Architecture)
- ❌ Local SQLite per developer
- ❌ Repo is just metadata
- ❌ No cross-repo relationships
- ❌ No team ownership
- ❌ Context is repo-agnostic

### Target (Multi-Team MVP)
- ✅ Shared memory server (one source of truth)
- ✅ Repositories as first-class entities
- ✅ Cross-repo dependency tracking
- ✅ Team ownership & visibility
- ✅ Dependency-aware context

---

## MVP Phases

### Phase 0: Multi-Repo Data Model (3-4 days)

**Goal:** Add repo/team primitives to data model

#### 0.1 Schema Changes

**New tables:**

```sql
-- Repositories table
CREATE TABLE repositories (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,           -- "auth-service"
    type TEXT DEFAULT 'service',         -- service, library, frontend, etc.
    path TEXT,                            -- local path or git URL
    owner_team TEXT,                      -- which team owns it
    metadata TEXT DEFAULT '{}',           -- additional info
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Repository dependencies
CREATE TABLE repo_dependencies (
    id TEXT PRIMARY KEY,
    source_repo TEXT NOT NULL,            -- "api-gateway"
    depends_on_repo TEXT NOT NULL,        -- "auth-service"
    dependency_type TEXT,                 -- "runtime", "build", "data"
    strength REAL DEFAULT 0.5,            -- how tightly coupled
    FOREIGN KEY (source_repo) REFERENCES repositories(name),
    FOREIGN KEY (depends_on_repo) REFERENCES repositories(name)
);

-- Teams
CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Modify memories table to add repo context
ALTER TABLE memories ADD COLUMN repo TEXT REFERENCES repositories(name);
ALTER TABLE memories ADD COLUMN team TEXT REFERENCES teams(name);
ALTER TABLE memories ADD COLUMN visibility TEXT DEFAULT 'organization'; -- team, organization, public
ALTER TABLE memories ADD COLUMN affects_repos TEXT DEFAULT '[]'; -- JSON array of repo names
```

**Files to modify:**
- `src/llm_memory/core/storage.py` - Add schema, new methods
- `src/llm_memory/core/repository.py` (NEW) - Repository management class

#### 0.2 Repository Management API

```python
# src/llm_memory/core/repository.py

class RepositoryManager:
    """Manage repositories and their relationships."""

    def register_repo(
        self,
        name: str,
        path: str = None,
        owner_team: str = None,
        type: str = "service"
    ) -> str:
        """Register a repository in the memory system."""

    def add_dependency(
        self,
        source: str,
        depends_on: str,
        type: str = "runtime",
        strength: float = 0.5
    ):
        """Declare that source repo depends on depends_on repo."""

    def get_dependencies(
        self,
        repo: str,
        depth: int = 1,
        direction: str = "both"  # upstream, downstream, both
    ) -> Dict[str, List[str]]:
        """Get dependency tree for a repo."""

    def get_affected_repos(
        self,
        repo: str,
        include_indirect: bool = True
    ) -> List[str]:
        """Get all repos that would be affected by changes to this repo."""

    def detect_dependencies_from_code(
        self,
        repo_path: Path
    ) -> List[Tuple[str, str]]:
        """
        Auto-detect dependencies from package.json, go.mod, etc.

        Returns: [(dependency_name, dependency_type), ...]
        """
```

#### 0.3 Update Memory API

**Modify `Memory` class to be repo-aware:**

```python
# src/llm_memory/core/memory.py

class Memory:
    def __init__(self, config: MemoryConfig = None, current_repo: str = None):
        """
        Args:
            current_repo: The repository context (auto-detected from git if None)
        """
        self.current_repo = current_repo or self._detect_current_repo()
        self.repos = RepositoryManager(self._storage)

    def record(
        self,
        event: str,
        category: str = "note",
        importance: float = 0.5,
        repo: str = None,  # NEW: defaults to current_repo
        affects_repos: List[str] = None,  # NEW: cross-repo impact
        team: str = None,  # NEW: team ownership
        visibility: str = "organization",  # NEW: who can see this
        **kwargs
    ) -> str:
        """Record event with repo context."""
        repo = repo or self.current_repo

        # Store with repo context
        return self.episodic.record(
            content=event,
            category=category,
            importance=importance,
            repo=repo,
            affects_repos=affects_repos or [],
            team=team,
            visibility=visibility,
            **kwargs
        )

    def context(
        self,
        repo: str = None,
        include_dependencies: bool = True,
        dependency_depth: int = 1,
        time_window_days: int = 30,
        format: str = "text"
    ) -> Any:
        """
        Get context for a repo, including relevant memories from dependencies.

        Args:
            repo: Repository to get context for (defaults to current)
            include_dependencies: Include memories from dependent repos
            dependency_depth: How many levels of dependencies to include
            time_window_days: Only include memories from last N days
        """

    def _detect_current_repo(self) -> Optional[str]:
        """Auto-detect current repo from git remote."""
        try:
            import git
            repo = git.Repo(search_parent_directories=True)
            # Extract repo name from remote URL
            remote_url = repo.remotes.origin.url
            # Parse: git@github.com:org/repo-name.git → repo-name
            name = remote_url.split('/')[-1].replace('.git', '')
            return name
        except:
            return None
```

**CLI updates:**

```bash
# New repo management commands
llm-memory repo register auth-service --path ./auth --team platform
llm-memory repo depends api-gateway auth-service --type runtime
llm-memory repo list
llm-memory repo graph  # ASCII visualization of dependencies

# Modified record command
llm-memory record "Changed JWT refresh to 7 days" \
  --repo auth-service \
  --affects api-gateway,user-service,mobile-app \
  --breaking

# Repo-aware context
llm-memory context --repo api-gateway --include-deps
```

**Testing checklist:**
- [ ] Can register repos
- [ ] Can declare dependencies
- [ ] `memory.record()` stores repo context
- [ ] `memory.context(repo=X)` returns repo-specific context
- [ ] Auto-detection of current repo works

---

### Phase 1: Shared Memory Server (2-3 days)

**Goal:** One central memory server that all team members connect to

#### 1.1 Server Architecture

**Two modes:**
- `local` (current): SQLite file, single user
- `shared` (new): HTTP server, multi-user

```python
# src/llm_memory/server/__init__.py (NEW)
# src/llm_memory/server/api.py (NEW)

from fastapi import FastAPI, Depends, HTTPException
from typing import Optional

app = FastAPI(title="LLM Memory Server")

# Authentication (simple token-based for MVP)
def verify_token(token: str):
    # Check against configured tokens
    # MVP: Just check env var LLM_MEMORY_TOKENS
    pass

@app.post("/memories")
async def create_memory(
    content: str,
    category: str,
    repo: str,
    team: Optional[str] = None,
    affects_repos: Optional[List[str]] = None,
    token: str = Depends(verify_token)
):
    """Record a memory."""

@app.get("/context")
async def get_context(
    repo: str,
    include_deps: bool = True,
    token: str = Depends(verify_token)
):
    """Get context for a repo."""

@app.get("/search")
async def search_memories(
    query: str,
    repos: Optional[List[str]] = None,
    teams: Optional[List[str]] = None,
    token: str = Depends(verify_token)
):
    """Search across memories."""
```

#### 1.2 Client Configuration

```yaml
# llm-memory.yaml

mode: shared  # or "local"

shared:
  server_url: "http://memory.company.internal:8080"
  token: "${LLM_MEMORY_TOKEN}"  # From env var

# Fallback to local if server unreachable
fallback_to_local: true
```

```python
# src/llm_memory/core/storage.py - Modify to support remote storage

class Storage:
    def __init__(self, config: StorageConfig):
        if config.mode == "shared":
            self.backend = RemoteStorageBackend(config.server_url, config.token)
        else:
            self.backend = LocalStorageBackend(config.data_dir)
```

#### 1.3 Deployment

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -e ".[all]"

# Use persistent volume for data
VOLUME /data

ENV LLM_MEMORY_DATA_DIR=/data
ENV LLM_MEMORY_TOKENS=token1,token2,token3

CMD ["llm-memory", "serve", "--host", "0.0.0.0", "--port", "8080"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  llm-memory:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - LLM_MEMORY_TOKENS=${LLM_MEMORY_TOKENS}
```

**Testing checklist:**
- [ ] Server starts and responds to health checks
- [ ] Can create memories via HTTP API
- [ ] Can retrieve context via HTTP API
- [ ] CLI connects to remote server when configured
- [ ] MCP server works with shared backend

---

### Phase 2: Cross-Repo Context (2-3 days)

**Goal:** The killer feature - get relevant context from dependent repos

#### 2.1 Enhanced Context Generation

```python
# src/llm_memory/core/memory.py

def context(
    self,
    repo: str = None,
    include_dependencies: bool = True,
    dependency_depth: int = 1,
    time_window_days: int = 30,
    categories: List[str] = None,
    format: str = "text"
) -> Any:
    """
    Generate cross-repo aware context.

    Example:
        Working in: api-gateway
        Dependencies: auth-service, database-lib
        Dependents: web-frontend, mobile-app

        Returns:
        - Breaking changes in auth-service (last 30 days)
        - Warnings from database-lib
        - Decisions affecting api-gateway
        - Known issues in dependencies
    """
    repo = repo or self.current_repo
    if not repo:
        # Fallback to old behavior
        return self._context_legacy(format=format)

    context = {
        "repo": repo,
        "dependencies": {},
        "warnings": [],
        "breaking_changes": [],
        "decisions": [],
        "knowledge": []
    }

    # Get dependency tree
    if include_dependencies:
        deps = self.repos.get_dependencies(
            repo,
            depth=dependency_depth,
            direction="upstream"  # What this repo depends on
        )

        for dep_repo in deps.get("upstream", []):
            # Get breaking changes from dependency
            breaking = self._get_breaking_changes(
                repo=dep_repo,
                since_days=time_window_days
            )
            context["breaking_changes"].extend(breaking)

            # Get warnings from dependency
            warnings = self.semantic.get_warnings(repo=dep_repo)
            context["warnings"].extend(warnings)

    # Get repo-specific knowledge
    repo_knowledge = self.semantic.relevant_for(
        repo=repo,
        limit=10
    )
    context["knowledge"] = repo_knowledge

    # Get decisions affecting this repo
    decisions = self.episodic.search(
        query=f"repo:{repo}",
        category="architecture_decision",
        time_window_days=time_window_days
    )
    context["decisions"] = decisions

    # Get cross-repo impact
    affected_by = self._get_affected_by(repo, time_window_days)
    context["affected_by"] = affected_by

    return self._format_cross_repo_context(context, format)

def _get_breaking_changes(
    self,
    repo: str,
    since_days: int
) -> List[Dict]:
    """Get breaking changes from a repo."""
    return self.episodic.search(
        query=f"repo:{repo}",
        tags=["breaking_change"],
        since_days=since_days
    )

def _get_affected_by(
    self,
    repo: str,
    since_days: int
) -> List[Dict]:
    """
    Get memories from OTHER repos that declare they affect this repo.

    Example: auth-service records "Changed JWT refresh affects: [api-gateway, user-service]"
             When in api-gateway, this shows up.
    """
    return self._storage.get_memories_affecting_repo(repo, since_days)
```

#### 2.2 Context Output Format

```markdown
# Memory Context: api-gateway

## Current Repository
**api-gateway** (service, owned by backend-team)

## Dependency Tree
Depends on:
  - auth-service (runtime)
  - database-lib (runtime)
  - logging-lib (build)

Depended on by:
  - web-frontend
  - mobile-app

---

## ⚠️ Breaking Changes (last 30 days)

**auth-service** - 2 days ago - @platform-team
> Changed JWT refresh token expiry from 30 days to 7 days. Clients need to handle refresh more frequently.
> **Affects:** api-gateway, user-service, mobile-app
> [See full context: memory://abc123]

---

## 🔴 Warnings

**auth-service/token.py**
> Race condition possible during token refresh. Always use mutex locks.
> [From: Bug fix on 2025-12-15]

**database-lib**
> Connection pool size must be tuned per service. Default (10) is too low for high-traffic services.

---

## 📋 Recent Decisions

**Why we use Redis for session storage** - 14 days ago
> Stateless scaling needed for Kubernetes deployment. Evaluated: Sessions (sticky), Redis (chosen), DynamoDB (cost)
> [Decision: memory://xyz789]

---

## 💡 Relevant Knowledge

- API Gateway must validate JWT signatures locally, not via auth-service call (performance)
- Rate limiting is per-service, not global
- All errors should include trace-id header for debugging

---

## 🔄 Recent Activity

- [3 days ago] Added circuit breaker for auth-service calls
- [5 days ago] Fixed timeout handling in gRPC interceptor
- [1 week ago] Upgraded to database-lib v2.3 (connection pooling fix)
```

#### 2.3 MCP Server Enhancements

```python
# src/llm_memory/interfaces/mcp.py

@mcp.tool()
async def memory_cross_repo_context(
    repo: str,
    include_dependencies: bool = True,
    time_window_days: int = 30
) -> str:
    """
    Get cross-repo aware context for a repository.

    Includes:
    - Breaking changes from dependencies
    - Warnings from related repos
    - Decisions affecting this repo
    - Known issues in dependency tree

    Use this when starting work in a repository to get full context.
    """

@mcp.tool()
async def memory_impact_analysis(
    repo: str,
    change_description: str
) -> str:
    """
    Analyze what repos would be affected by a change.

    Args:
        repo: Repository making the change
        change_description: What's changing

    Returns:
        List of affected repos with reasons
    """
    affected = memory.repos.get_affected_repos(repo)

    # Use LLM to analyze which are actually affected
    # Based on change description + historical patterns
```

**Testing checklist:**
- [ ] Context shows breaking changes from dependencies
- [ ] Warnings from other repos appear when relevant
- [ ] Can see what repos depend on current repo
- [ ] MCP tools work with cross-repo context

---

### Phase 3: Automatic Capture (1-2 days)

**Goal:** Reduce manual effort - capture from git automatically

#### 3.1 Git Hook Enhancement

```python
# src/llm_memory/capture/git.py - Enhance to be repo-aware

class GitCapture:
    def capture_commit(
        self,
        repo_name: str,
        commit_hash: str,
        detect_breaking: bool = True,
        detect_affected: bool = True
    ):
        """
        Capture commit with cross-repo awareness.

        Auto-detects:
        - Is this a breaking change? (via commit message, file patterns)
        - What repos does this affect? (via code analysis)
        """
        commit = self.repo.commit(commit_hash)

        # Parse conventional commit
        category = self._parse_commit_type(commit.message)

        # Detect breaking changes
        is_breaking = "BREAKING" in commit.message or "!" in commit.message.split('\n')[0]

        # Analyze affected repos
        affected_repos = []
        if detect_affected:
            affected_repos = self._detect_affected_repos(commit)

        # Record with full context
        self.memory.record(
            event=f"{commit.message}",
            category=category,
            repo=repo_name,
            affects_repos=affected_repos,
            tags=["breaking_change"] if is_breaking else [],
            metadata={
                "commit_hash": commit_hash,
                "author": commit.author.name,
                "files_changed": [f.path for f in commit.stats.files]
            }
        )

    def _detect_affected_repos(self, commit) -> List[str]:
        """
        Analyze commit to detect which repos might be affected.

        Heuristics:
        - Changed API contracts? → Affects downstream services
        - Changed shared lib? → Affects all dependents
        - Updated proto files? → Affects gRPC clients
        - Modified config? → Might affect deployment
        """
        affected = []

        for file in commit.stats.files:
            # Check file patterns
            if 'proto/' in file.path or file.path.endswith('.proto'):
                # Proto file changed - affects all gRPC clients
                affected.extend(self._get_grpc_clients())
            elif 'api/' in file.path or 'endpoint' in file.path.lower():
                # API changed - affects clients
                affected.extend(self._get_api_clients())
            elif file.path.endswith(('package.json', 'go.mod', 'requirements.txt')):
                # Dependency changed - check if shared lib
                if self.repo_type == 'library':
                    affected.extend(self._get_dependents())

        return list(set(affected))
```

#### 3.2 GitHub Actions Integration

```yaml
# .github/workflows/llm-memory-capture.yml

name: Capture to LLM Memory

on:
  push:
    branches: [main, master, develop]
  pull_request:
    types: [opened, closed]

jobs:
  capture:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install llm-memory
        run: pip install llm-memory[capture]

      - name: Configure memory server
        run: |
          echo "LLM_MEMORY_MODE=shared" >> $GITHUB_ENV
          echo "LLM_MEMORY_SERVER_URL=${{ secrets.MEMORY_SERVER_URL }}" >> $GITHUB_ENV
          echo "LLM_MEMORY_TOKEN=${{ secrets.MEMORY_TOKEN }}" >> $GITHUB_ENV

      - name: Capture commit
        if: github.event_name == 'push'
        run: |
          llm-memory capture commit \
            --repo ${{ github.event.repository.name }} \
            --hash ${{ github.sha }} \
            --detect-breaking \
            --detect-affected

      - name: Capture PR
        if: github.event_name == 'pull_request' && github.event.action == 'closed' && github.event.pull_request.merged
        run: |
          llm-memory capture pr \
            --repo ${{ github.event.repository.name }} \
            --number ${{ github.event.pull_request.number }} \
            --extract-decisions
```

#### 3.3 CLI Commands

```bash
# Manual capture
llm-memory capture commit --hash abc123 --repo auth-service
llm-memory capture pr --number 42 --repo api-gateway
llm-memory capture release --version v2.0.0 --repo auth-service

# Install git hooks for auto-capture
llm-memory capture install --repo auth-service

# One-time sync of history
llm-memory capture sync --repo auth-service --since "3 months ago"
```

**Testing checklist:**
- [ ] Git commits auto-captured on push
- [ ] Breaking changes detected from commit messages
- [ ] Affected repos detected from file changes
- [ ] GitHub Actions workflow runs successfully
- [ ] Can manually trigger capture for old commits

---

## Testing with Teams

### Week 1: Internal Testing (1-2 repos, 1 team)

**Setup:**
```bash
# Deploy shared server
docker-compose up -d

# Register your repos
llm-memory repo register auth-service --team platform
llm-memory repo register api-gateway --team backend
llm-memory repo depends api-gateway auth-service

# Install GitHub Actions in both repos
# Configure team members' machines
export LLM_MEMORY_MODE=shared
export LLM_MEMORY_SERVER_URL=http://localhost:8080
export LLM_MEMORY_TOKEN=test-token
```

**What to test:**
1. Make a change in auth-service, record it as breaking
2. Open api-gateway, run `llm-memory context`
3. Verify it shows the breaking change from auth-service
4. Use Claude Code with MCP server - does it surface relevant context?

**Success metrics:**
- [ ] Team uses `llm-memory context` at least once per day
- [ ] Context is relevant (>70% of shown memories are useful)
- [ ] Team members find dependencies they didn't know about
- [ ] Faster onboarding for new team member

### Week 2-3: Expand (3-5 repos, 2-3 teams)

**Setup:**
- Add 3 more repos with real dependency relationships
- Have 2-3 teams use it for real work
- Enable GitHub Actions auto-capture

**What to observe:**
- Do teams manually record decisions?
- Do they check context before making changes?
- Do they trust the "affects repos" predictions?
- What friction points exist?

**Interviews:**
Ask each team:
1. "Did you discover anything from memory that you wouldn't have found otherwise?"
2. "What memories were irrelevant or wrong?"
3. "Would you keep using this after the test?"
4. "What's missing?"

### Success Criteria

**STOP building if:**
- < 50% of team members use it weekly
- < 60% of surfaced memories are relevant
- Teams say "Slack search + docs is easier"
- No one manually records anything (only auto-capture used)

**CONTINUE building if:**
- > 70% weekly active users
- > 70% memory relevance
- Teams request features ("Can we add X?")
- At least some manual recording happens
- At least one "saved us from a bug" story

---

## What NOT to Build (Yet)

**Defer to later:**
- ❌ Smart compression (Phases 5)
- ❌ Pattern detection (Phase 3)
- ❌ Feedback loops (Phase 6)
- ❌ Multi-agent sync (Phase 7)
- ❌ Complex permission system (org-wide is fine for MVP)
- ❌ Web UI (CLI + MCP is enough to test)
- ❌ LLM-powered relationship detection (manual declaration is fine)

**Why:** Test if the core value prop works first. If teams don't use basic cross-repo context, fancy features won't help.

---

## Implementation Order

### Week 1
**Days 1-2:** Phase 0.1 - Schema changes, repository table
**Days 3-4:** Phase 0.2 - Repository manager API
**Day 5:** Phase 0.3 - Update Memory API to be repo-aware

### Week 2
**Days 1-2:** Phase 1 - Shared server (FastAPI)
**Day 3:** Phase 1 - Client configuration, deployment
**Days 4-5:** Phase 2.1 - Cross-repo context generation

### Week 3
**Days 1-2:** Phase 2.2 - Context formatting, MCP enhancements
**Days 3-4:** Phase 3 - Git capture automation
**Day 5:** Documentation, team onboarding

### Week 4+
**Testing with teams, iteration based on feedback**

---

## Migration Path

**For existing llm-memory users:**

```bash
# Backup existing data
llm-memory export --output backup.json

# Upgrade
pip install --upgrade llm-memory

# Run migration
llm-memory migrate to-multi-repo --detect-repo-from-git

# Optionally switch to shared mode
llm-memory config set mode shared
llm-memory config set server_url http://memory.company.internal:8080
```

---

## Architecture Comparison

### Before (Solo)
```
┌─────────────────┐
│   Developer     │
│                 │
│  ┌───────────┐  │
│  │ SQLite DB │  │
│  └───────────┘  │
│  ┌───────────┐  │
│  │ ChromaDB  │  │
│  └───────────┘  │
└─────────────────┘
```

### After (Multi-Team)
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Team A     │  │   Team B     │  │   Team C     │
│              │  │              │  │              │
│  ┌────────┐  │  │  ┌────────┐  │  │  ┌────────┐  │
│  │ Claude │  │  │  │ Cursor │  │  │  │  Aider │  │
│  └────┬───┘  │  │  └────┬───┘  │  │  └────┬───┘  │
└───────┼──────┘  └───────┼──────┘  └───────┼──────┘
        │                 │                 │
        └────────┬────────┴────────┬────────┘
                 │                 │
         ┌───────▼─────────────────▼───────┐
         │  LLM Memory Shared Server       │
         │                                  │
         │  ┌────────────────────────────┐ │
         │  │  Repository Graph          │ │
         │  │  - auth-service            │ │
         │  │  - api-gateway → auth      │ │
         │  │  - user-service → auth     │ │
         │  │  - mobile-app → api        │ │
         │  └────────────────────────────┘ │
         │                                  │
         │  ┌────────────────────────────┐ │
         │  │  Team Memories             │ │
         │  │  - Platform team           │ │
         │  │  - Backend team            │ │
         │  │  - Mobile team             │ │
         │  └────────────────────────────┘ │
         │                                  │
         │  SQLite + ChromaDB               │
         └──────────────────────────────────┘
```

---

## Next Steps

1. **Review this plan** - Does this match your vision?
2. **Start Phase 0** - Schema changes and repo model
3. **Get 1 team to commit** to testing in Week 4
4. **Build MVP** - 3 weeks focused work
5. **Test with teams** - 2-3 weeks validation
6. **Decide:** Kill it, pivot it, or scale it

Remember: **The goal is learning, not building everything.** If teams don't find basic cross-repo context useful, no amount of compression or pattern detection will fix it.
