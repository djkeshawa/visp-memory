# Documentation

Visp Memory stores project knowledge for coding assistants. Start with a local
SQLite store, then add the interfaces and optional search providers you need.

```
docs/
├── guides/        How to install, connect, and operate Visp Memory
├── reference/     How it works, what it guarantees, and what has been measured
└── development/   How to test and release the project
```

## Guides

| Guide | What it answers |
|---|---|
| [Installation](guides/INSTALLATION.md) | How do I run Docker, Python, or a standalone download? |
| [Accounts and tokens](guides/AUTHENTICATION.md) | How do I sign in and connect an API client? |
| [MCP and hooks](guides/MCP.md) | How does my assistant use project memory? |
| [Storage and embeddings](guides/STORAGE.md) | Where is data stored, how does semantic search work, and how do I back it up? |
| [Dreaming](guides/DREAMING.md) | What is merged automatically, what needs review, and how do I undo it? |
| [Workflow reports](guides/WORKFLOW_REPORTS.md) | How do explicit assistant reports update intent status? |

## Reference

| Reference | What it answers |
|---|---|
| [Feature status](reference/FEATURE_STATUS.md) | Which features are supported, beta, experimental, or frozen? |
| [Architecture](reference/ARCHITECTURE.md) | How do capture, storage, ranking, and context selection fit together? |
| [Trust and privacy](reference/TRUST.md) | What can enter a prompt, and what data leaves the machine? |
| [Contracts](reference/CONTRACTS.md) | Which machine interfaces and storage guarantees are stable? |
| [Benchmarks](reference/BENCHMARK.md) | What has been measured, how to reproduce it, and its limits |

## Development

- [Contributing](../CONTRIBUTING.md): setup, sign-off, and pull request requirements.
- [Testing](development/TESTING.md): test layout, required checks, and backend integration tests.
- [Releasing](development/RELEASING.md): maintainer build and publication procedure.

Release notes and downloads live on [GitHub Releases](https://github.com/djkeshawa/visp-memory/releases).
Update an existing page rather than adding a new one, and keep this index as the
route to every topic.
