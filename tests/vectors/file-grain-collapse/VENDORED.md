# Provenance of this vendored directory

Everything beside this file is **intel's**, copied verbatim. `README.md` is intel's
too and describes intel's regeneration workflow, not Memory's; Memory does not
regenerate these bytes, it conforms to them.

## Where they came from, stated exactly as strongly as it can be

| | |
| --- | --- |
| source repository | `visp-intel`, same workspace |
| source path | `evaluation/vectors/file-grain-collapse/` |
| source state | **untracked in the working tree** at copy time |
| `visp-intel` HEAD at copy time | `f4c5b3b` |
| copied | 2026-08-12 |

The directory was **not committed in `visp-intel`** when it was copied, so there is no
commit id that resolves to these bytes and none is claimed here. `f4c5b3b` is the head
the working tree sat on; it does not contain the vectors. What *is* pinned is content:
`manifest.json` carries a SHA-256 and a byte length for every document, and
`tests/core/test_code_graph_conformance.py::test_vendored_vectors_match_their_manifest`
verifies all five on every run. If intel republishes, the hashes move and that test
fails, which is the intended way to find out.

Upgrade path: once intel commits the directory, replace this table's `source state` row
with the commit id and re-verify. Until then the honest statement is "these bytes, this
manifest, no commit".

## What Memory conforms to, and what it does not

Conformed, in `tests/core/test_code_graph_conformance.py`:

- the collapse of `projection.json` — file set, test files, dependency edges, external
  dependencies, test edges, and their order;
- all seven neighbourhood cases — hop map, seed classification, `truncated`, `reached`;
- the `consumerConventionEcho` proximity arithmetic, against Memory's own
  `PROXIMITY_DECAY`, which is a literal in `visp_memory/core/code_graph.py` and not a
  number intel supplied;
- `projection-superseded.json` refusing to load, because its `snapshotId` is not its
  `headSnapshotId`. Intel ships no `stale` flag; that judgement is Memory's and lives in
  `load_projection`.

Not conformed, deliberately:

- `expected.ascribedEdges` and the `counts` derived from it. That block carries every
  edge kind intel ascribes to a file — `defines`, `writes`, `calls` — and Memory
  collapses only the dependency and test kinds it acts on. Asserting the full block
  would be asserting a shape Memory does not build.
- The rejection *prose* in `seedsRejected`. Memory asserts which seeds are rejected;
  the wording of the reason is intel's and Memory writes its own.

## Holdout

`fixture-sources.json` is seven files authored inside `visp-intel` for this purpose.
Nothing here derives from a benchmark repository, a measured project, or any task under
`planning/private/holdout/`, and nothing that does may ever be added.
