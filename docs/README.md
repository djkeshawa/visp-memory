# Documentation

Visp Memory stores project knowledge for coding assistants. Start with a local
SQLite store, then choose the interfaces and optional search providers you need.

## Get started

| Guide | What it answers |
|---|---|
| [Installation](deployment/PACKAGING.md) | How do I run Docker, Python, or a standalone download? |
| [Accounts and tokens](deployment/AUTH.md) | How do I sign in and connect an API client? |
| [MCP and hooks](development/MCP.md) | How does my assistant use project memory? |
| [Feature status](FEATURE_STATUS.md) | Which features are supported or still early? |

## Understand and operate

| Guide | What it answers |
|---|---|
| [Architecture](development/ARCHITECTURE.md) | How do capture, storage, and retrieval fit together? |
| [Storage and embeddings](development/STORAGE.md) | Where is data stored, how does semantic search work, and how do I back it up? |
| [Dreaming](development/DREAMING.md) | What is merged automatically, what needs review, and how do I undo it? |
| [Workflow reports](development/WORKFLOW_REPORTS.md) | How do explicit assistant reports update intent status? |
| [Trust and privacy](TRUST.md) | What can enter a prompt, and what data leaves the machine? |

## Reference and development

- [Machine and storage contracts](development/CONTRACT_SURFACE.md): integration guarantees and backend limits.
- [Benchmarks](BENCHMARK.md): reproducible measurements, methods, and limitations.
- [Contributing](../CONTRIBUTING.md) and [testing](development/TESTING.md): development setup and verification.
- [Releasing](deployment/RELEASING.md): maintainer build and publication procedure.

Release notes and downloads have one home: [GitHub Releases](https://github.com/djkeshawa/visp-memory/releases).
