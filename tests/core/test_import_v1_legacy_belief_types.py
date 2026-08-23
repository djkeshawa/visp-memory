"""A v1 export's legacy semantic categories import instead of being refused.

The old build (<= 0.5.0) exported version "1.0" files whose semantic categories
predate the governed belief vocabulary — "fragile_area", "convention",
"known_issue". The v2/v3 import path migrates those through
``migrate_legacy_belief_fields``; the v1 path passed them straight into
validation, so the governed allow-list refused the write and the documented
export → import migration failed for every pre-schema-v3 export. Found live,
migrating a real deployment's 267 memories.

These tests pin the v1 treatment to match v2: the type is mapped, the original
name survives in metadata, and the row is flagged as unreviewed legacy
material.
"""

import json

from visp_memory import Memory
from visp_memory.config import MemoryConfig


def _memory(tmp_path, name):
    config = MemoryConfig(project_name=name, repo_id="repo-a")
    config.storage.data_dir = tmp_path / name
    config.embedding.provider = "noop"
    return Memory(config=config)


def _v1_export(tmp_path, semantic):
    path = tmp_path / "v1-export.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "exported_at": "2026-05-01T00:00:00+00:00",
                "memories": {
                    "episodic": [
                        {
                            "content": "Fixed the retry loop",
                            "category": "bug_fixed",
                            "importance": 0.8,
                        }
                    ],
                    "semantic": semantic,
                },
                "intents": [],
            }
        )
    )
    return path


def test_v1_fragile_area_imports_as_negative_with_legacy_name_kept(tmp_path):
    memory = _memory(tmp_path, "target")
    memory.import_memories(
        _v1_export(
            tmp_path,
            [
                {
                    "content": "The session module breaks under concurrent writes",
                    "category": "fragile_area",
                    "importance": 0.9,
                }
            ],
        )
    )

    rows = memory._storage.list_memories(repo_id="repo-a", layer="semantic")
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "negative"
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata.get("legacy_category") == "fragile_area"
    assert "legacy_unreviewed" in (row.get("quality_flags") or [])

    episodic = memory._storage.list_memories(repo_id="repo-a", layer="episodic")
    assert any("retry loop" in r["content"] for r in episodic)


def test_v1_unknown_semantic_category_degrades_to_hypothesis_not_refusal(tmp_path):
    memory = _memory(tmp_path, "target-unknown")
    memory.import_memories(
        _v1_export(
            tmp_path,
            [
                {
                    "content": "Some claim under a category no build ever defined",
                    "category": "definitely_not_a_category",
                }
            ],
        )
    )
    rows = memory._storage.list_memories(repo_id="repo-a", layer="semantic")
    assert len(rows) == 1
    assert rows[0]["category"] == "hypothesis"
