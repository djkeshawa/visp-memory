import re
from pathlib import Path

from visp_memory.config import load_config
from visp_memory.core.memory import Memory


def ingest_project(root_dir: str):
    config = load_config()
    memory = Memory(config=config)

    root_path = Path(root_dir).resolve()
    print(f"Ingesting project from: {root_path}")

    # Track created IDs to link them
    file_ids = {}  # path -> memory_id

    # 1. Walk and create nodes
    for path in root_path.rglob("*"):
        if any(p.startswith(".") for p in path.parts):
            continue
        if "__pycache__" in path.parts:
            continue
        if "node_modules" in path.parts:
            continue

        rel_path = path.relative_to(root_path)

        if path.is_dir():
            # Directory -> Semantic Memory
            mem_id = memory.learn(
                knowledge=f"Directory structure: {rel_path}",
                category="code_structure",
                importance=0.3,
            )
            file_ids[str(path)] = mem_id
            print(f"Dir: {rel_path}")

        elif path.is_file() and path.suffix in [".py", ".ts", ".tsx", ".md"]:
            # File -> Semantic Memory
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                summary = f"File {rel_path} with {len(content.splitlines())} lines."

                # Check for class/function defs for richer summary
                defs = re.findall(
                    r"^(?:class|def|function|export function|export const)\s+([a-zA-Z0-9_]+)",
                    content,
                    re.MULTILINE,
                )
                if defs:
                    summary += f" Defines: {', '.join(defs[:5])}..."

                mem_id = memory.learn(knowledge=summary, category="code_file", importance=0.5)
                file_ids[str(path)] = mem_id
                print(f"File: {rel_path}")

            except Exception as e:
                print(f"Skipping {rel_path}: {e}")

    # 2. Create Directory relationships (Hierarchy)
    print("\nCreating hierarchy relationships...")
    for path_str, mem_id in file_ids.items():
        path = Path(path_str)
        parent = path.parent
        if str(parent) in file_ids and str(parent) != str(
            root_path
        ):  # Don't link outside scanned area unless root is scanned
            pass

        # Simpler: check if parent is in file_ids
        if str(parent) in file_ids:
            parent_id = file_ids[str(parent)]
            memory._storage.add_relationship(parent_id, mem_id, "contains", 1.0)

    # 3. Create Import relationships (Relationships)
    print("\nCreating dependency relationships...")
    for path_str, mem_id in file_ids.items():
        path = Path(path_str)
        if not path.is_file():
            continue

        if path.suffix == ".py":
            # Python imports
            content = path.read_text(errors="ignore")
            # import x.y.z
            imports = re.findall(r"^(?:from|import)\s+([\w\.]+)", content, re.MULTILINE)
            for imp in imports:
                # Naively try to find module in file_ids
                # imp = visp_memory.core.memory -> src/visp_memory/core/memory.py
                parts = imp.split(".")

                # Try simple matching
                for other_path_str, other_id in file_ids.items():
                    if other_id == mem_id:
                        continue
                    other_path = Path(other_path_str)
                    if other_path.stem == parts[-1] or other_path.name == parts[-1] + ".py":
                        memory._storage.add_relationship(mem_id, other_id, "imports", 0.7)
                        # print(f"  {path.name} -> {other_path.name}")
                        break

    print("Ingestion complete!")


if __name__ == "__main__":
    import sys

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    ingest_project(root)
