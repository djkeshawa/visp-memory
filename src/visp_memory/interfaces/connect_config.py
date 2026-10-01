"""Edit client settings without silently discarding a project's config text."""

import json
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from visp_memory.interfaces.connect_models import ConnectError


def load_document(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        loaded = (
            yaml.safe_load(content) if path.suffix in {".yaml", ".yml"} else json.loads(content)
        )
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise ConnectError(f"Cannot read {path}: {error}") from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConnectError(f"{path} must contain a config object")
    return loaded


def _mapping_fields(node: MappingNode) -> dict | None:
    fields = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in fields:
            return None
        fields[key.value] = value
    return fields


def _line_end(content: str, offset: int) -> int:
    newline = content.find("\n", offset)
    return len(content) if newline < 0 else newline + 1


def _storage_edits(content: str, storage, values: dict) -> list | None:
    if isinstance(storage, ScalarNode) and storage.tag == "tag:yaml.org,2002:null":
        if storage.start_mark.line != storage.end_mark.line:
            return None
        end = _line_end(content, storage.end_mark.index)
        prefix = "\n" if end and content[end - 1] != "\n" else ""
        block = "".join(f"  {key}: {json.dumps(value)}\n" for key, value in values.items())
        return [
            (storage.start_mark.index, storage.end_mark.index, ""),
            (end, end, prefix + block),
        ]
    if not isinstance(storage, MappingNode) or storage.flow_style:
        return None
    fields = _mapping_fields(storage)
    if fields is None or "<<" in fields:
        return None
    edits = []
    missing = []
    indent = storage.start_mark.column
    for key, value in values.items():
        node = fields.get(key)
        value_text = json.dumps(value, ensure_ascii=False)
        if node is None:
            missing.append(f"{' ' * indent}{key}: {value_text}\n")
        elif isinstance(node, ScalarNode) and node.start_mark.line == node.end_mark.line:
            edits.append((node.start_mark.index, node.end_mark.index, value_text))
        else:
            return None
    if missing:
        last = storage.value[-1][1]
        # A block value may end at the next top-level key; do not consume it.
        end = last.end_mark.index
        if last.end_mark.column:
            end = _line_end(content, end)
        prefix = "\n" if end and content[end - 1] != "\n" else ""
        edits.append((end, end, prefix + "".join(missing)))
    return edits


def _edit_simple_yaml(content: str, merged: dict) -> str | None:
    # Anchors/aliases can share values outside the edited fields; rewriting a
    # single occurrence could silently change unrelated settings.
    if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(content)):
        return None
    root = yaml.compose(content)
    if root is None:
        prefix = "\n" if content and not content.endswith("\n") else ""
        return content + prefix + yaml.safe_dump(merged, sort_keys=False)
    if not isinstance(root, MappingNode) or root.flow_style:
        return None
    fields = _mapping_fields(root)
    if fields is None:
        return None
    edits = []
    additions = []
    repo = fields.get("repo_id")
    repo_text = json.dumps(merged["repo_id"], ensure_ascii=False)
    if repo is None:
        additions.append(f"repo_id: {repo_text}\n")
    elif isinstance(repo, ScalarNode) and repo.start_mark.line == repo.end_mark.line:
        edits.append((repo.start_mark.index, repo.end_mark.index, repo_text))
    else:
        return None

    storage = fields.get("storage")
    values = {key: merged["storage"][key] for key in ("mode", "server_url")}
    if storage is None:
        additions.append("storage:\n" + "".join(
            f"  {key}: {json.dumps(value)}\n" for key, value in values.items()
        ))
    else:
        storage_edits = _storage_edits(content, storage, values)
        if storage_edits is None:
            return None
        edits.extend(storage_edits)
    if additions:
        prefix = "\n" if content and not content.endswith("\n") else ""
        edits.append((len(content), len(content), prefix + "".join(additions)))
    for start, end, replacement in sorted(edits, reverse=True):
        content = content[:start] + replacement + content[end:]
    # Marks handle quoted '#' and multiline values; equality catches any
    # structure too unusual for this deliberately minimal editor.
    return content if yaml.safe_load(content) == merged else None


def _save_backup(path: Path) -> Path:
    backup = path.with_name(path.name + ".backup")
    number = 0
    while backup.exists():
        number += 1
        backup = path.with_name(f"{path.name}.backup.{number}")
    with backup.open("xb") as stream:
        stream.write(path.read_bytes())
    return backup


def write_client_config(path: Path, document: dict, repo_id: str, server_url: str) -> str | None:
    merged = {**document, "repo_id": repo_id, "storage": {
        **(document.get("storage") or {}), "mode": "client", "server_url": server_url,
    }}
    try:
        original = path.read_bytes().decode("utf-8") if path.exists() else ""
        if path.suffix not in {".yaml", ".yml"}:
            content = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        else:
            content = _edit_simple_yaml(original, merged)
        note = None
        if content is None:
            content = yaml.safe_dump(merged, sort_keys=False)
            if original != content:
                backup = _save_backup(path)
                note = f"Saved {backup}; YAML comments were not kept in {path}."
        if original != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        return note
    except (OSError, yaml.YAMLError) as error:
        raise ConnectError(f"Cannot write {path}: {error}") from error
