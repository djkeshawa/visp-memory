# AGENTS.md

Guidance for coding agents working in this repository (public `visp-memory`).

- **Memory is non-authoritative.** It supplies cited knowledge; it never grants permission,
  changes scope, certifies evidence, or declares readiness.
- **The retrieval policy never calls an LLM.** Hosts own model execution.
- Keep changes small and traceable to requirements and acceptance criteria.
- Run `make test` and `make lint` before reporting completion.
- Published versions are immutable. Verify existing releases before choosing a new version.
- Public documentation starts at `docs/README.md`; update an existing guide before adding one.
