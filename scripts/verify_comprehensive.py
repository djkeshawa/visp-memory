
import asyncio
import json
import os
import shutil
import pytest
from pathlib import Path
from llm_memory.core.memory import Memory
from llm_memory.config import MemoryConfig
from llm_memory.core.neo4j_storage import Neo4jStorage

# Test Configuration
TEST_REPO_A = "repo_a_project"
TEST_REPO_B = "repo_b_isolated"
TEST_DIR = Path("test_comprehensive_audit")

def setup_environment():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir()

def test_cli_overrides():
    """Test that CLI-style overrides work directly on Memory methods."""
    print("Testing Method Overrides...")
    config = MemoryConfig(project_name="audit_test", repo_id=TEST_REPO_A)
    config.storage.data_dir = TEST_DIR / "data"
    memory = Memory(config=config)
    
    # 1. Record in Repo A (Default)
    mem_a = memory.record("Event in Repo A")
    
    # 2. Record in Repo B (Override)
    mem_b = memory.record("Event in Repo B", repo_id=TEST_REPO_B)
    
    # 3. Learn in Repo B (Override) - Newly added support
    mem_learn_b = memory.learn("Knowledge in Repo B", repo_id=TEST_REPO_B)
    
    # 4. Goal in Repo B (Override) - Newly added support
    intent_b = memory.goal("Goal in Repo B", repo_id=TEST_REPO_B)
    
    # Verify separation
    storage = memory._storage
    
    # check A
    res_a = storage.list_memories(repo_id=TEST_REPO_A, limit=10)
    print(f"Repo A memories: {len(res_a)}")
    assert len(res_a) >= 1
    assert any("Event in Repo A" in m['content'] for m in res_a)
    assert not any("Repo B" in m['content'] for m in res_a)
    
    # check B
    res_b = storage.list_memories(repo_id=TEST_REPO_B, limit=10)
    print(f"Repo B memories: {len(res_b)}")
    assert len(res_b) >= 2 # 1 record + 1 learn
    assert any("Event in Repo B" in m['content'] for m in res_b)
    assert any("Knowledge in Repo B" in m['content'] for m in res_b)
    
    # check Intents B
    intents_b = storage.get_active_intents(repo_id=TEST_REPO_B)
    print(f"Repo B intents: {len(intents_b)}")
    assert len(intents_b) >= 1
    assert intents_b[0]['description'] == "Goal in Repo B"
    
    print("✅ Method Overrides Verified\n")

def test_api_isolation():
    """Test API endpoint isolation (simulated)."""
    print("Testing API Isolation...")
    # We can simulate API calls by checking what the API calls would do:
    # list_memories(repo_id=...)
    
    config = MemoryConfig(project_name="audit_test", repo_id=TEST_REPO_A)
    memory = Memory(config=config)
    storage = memory._storage
    
    # Using previous data...
    
    # Test list_memories with repo_id
    api_res_a = storage.list_memories(repo_id=TEST_REPO_A)
    api_res_b = storage.list_memories(repo_id=TEST_REPO_B)
    
    assert len(api_res_a) != len(api_res_b)
    
    # Test graph relationships (requires creating one)
    # Let's create a relationship in Repo B
    mem_learn_b_id = [m['id'] for m in api_res_b if "Knowledge" in m['content']][0]
    mem_record_b_id = [m['id'] for m in api_res_b if "Event" in m['content']][0]
    
    storage.add_relationship(mem_record_b_id, mem_learn_b_id, "related_to")
    
    # Verify graph isolation
    rels_a = storage.get_all_relationships(repo_id=TEST_REPO_A)
    rels_b = storage.get_all_relationships(repo_id=TEST_REPO_B)
    
    print(f"Repo A Rels: {len(rels_a)}")
    print(f"Repo B Rels: {len(rels_b)}")
    
    assert len(rels_b) >= 1
    assert len(rels_a) == 0
    
    print("✅ API Isolation Verified\n")

def test_import_export_isolation():
    """Test that import/export respects repo_id."""
    print("Testing Import/Export Isolation...")
    
    config = MemoryConfig(project_name="audit_test", repo_id=TEST_REPO_B) # Configured for B
    memory = Memory(config=config)
    
    # Export B
    export_file = TEST_DIR / "export_b.json"
    memory.export(export_file)
    
    data = json.loads(export_file.read_text())
    
    # Verify export content
    episodic_count = len(data["memories"]["episodic"])
    semantic_count = len(data["memories"]["semantic"])
    intent_count = len(data["intents"])
    
    print(f"Exported B: {episodic_count} Ep, {semantic_count} Sem, {intent_count} Int")
    
    assert episodic_count >= 1
    assert semantic_count >= 1
    assert intent_count >= 1
    
    # Test Import into NEW Repo C
    TEST_REPO_C = "repo_c_import_target"
    config_c = MemoryConfig(project_name="audit_test", repo_id=TEST_REPO_C)
    memory_c = Memory(config=config_c)
    
    # But wait, import_memories reads from file and calls store_memory.
    # It attempts to use repo_id from file first. 
    # My updated code: repo_id = mem.get("repo_id") or self.config.repo_id
    
    # If I want to import B's data INTO C, I need the file to NOT have repo_id OR I need to force it?
    # If the file HAS repo_id (which export includes), it will import back into B!
    # Unless I modify the file to remove repo_id or simulate a "migration".
    
    # Let's see if export includes repo_id. 
    # My export code: repo_id=mem.get("repo_id") ... wait, export calls list_memories.
    # The returned dicts HAVE repo_id.
    
    # So if I import, it will use B's repo_id.
    # This is correct behavior for Backup/Restore.
    # But if I want to "clone" to another repo, I'd need to strip it.
    
    # Let's verify Backup/Restore behavior (it puts it back into B effectively, or updates it).
    # To test isolation, let's manually strip repo_id from file and see if it goes into C (config default).
    
    for layer in ["episodic", "semantic"]:
        for m in data["memories"][layer]:
            if "repo_id" in m: del m["repo_id"]
            
    modified_export = TEST_DIR / "export_b_stripped.json"
    modified_export.write_text(json.dumps(data))
    
    memory_c.import_memories(modified_export)
    
    # Check C
    res_c = memory_c._storage.list_memories(repo_id=TEST_REPO_C)
    print(f"Repo C memories: {len(res_c)}")
    assert len(res_c) > 0
    
    print("✅ Import/Export Isolation Verified\n")

if __name__ == "__main__":
    setup_environment()
    try:
        test_cli_overrides()
        test_api_isolation()
        test_import_export_isolation()
        print("🎉 ALL CHECKS PASSED")
    except Exception as e:
        print(f"❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
