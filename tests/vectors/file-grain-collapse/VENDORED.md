# Provenance of this vendored directory

Everything beside this file is copied verbatim from `visp-intel`
(`evaluation/vectors/file-grain-collapse/`). `README.md` describes that project's
regeneration workflow; Memory does not regenerate these bytes, it conforms to them.

The copy is pinned by content, not by commit: `manifest.json` carries a SHA-256 and a
byte length for every document, and
`tests/core/test_code_graph_conformance.py::test_vendored_vectors_match_their_manifest`
verifies them on every run. If the upstream vectors change, the hashes move and that
test fails, which is the intended way to find out.

## What Memory conforms to, and what it does not

Conformed, in `tests/core/test_code_graph_conformance.py`:

- the collapse of `projection.json` — file set, test files, dependency edges, external
  dependencies, test edges, and their order;
- all seven neighbourhood cases — hop map, seed classification, `truncated`, `reached`;
- the `consumerConventionEcho` proximity arithmetic, against Memory's own
  `PROXIMITY_DECAY`, which is a literal in `visp_memory/core/code_graph.py`;
- `projection-superseded.json` refusing to load, because its `snapshotId` is not its
  `headSnapshotId`. The vectors ship no `stale` flag; that judgement is Memory's and
  lives in `load_projection`.

Not conformed, deliberately:

- `expected.ascribedEdges` and the `counts` derived from it. That block carries every
  edge kind ascribed to a file — `defines`, `writes`, `calls` — and Memory collapses
  only the dependency and test kinds it acts on.
- The rejection *prose* in `seedsRejected`. Memory asserts which seeds are rejected;
  it writes its own wording for the reason.

## Fixture origin

`fixture-sources.json` is seven small files authored for this purpose. Nothing here
derives from a real repository or a benchmark task, and nothing that does may be added.
