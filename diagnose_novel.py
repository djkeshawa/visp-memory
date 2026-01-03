#!/usr/bin/env python3
"""
Diagnostic script for 'novel' project issues.
Tests:
1. Learn command with repo_id
2. Recall command with repo_id
3. Stats command filtering
"""

from llm_memory.core.memory import Memory
from llm_memory.config import MemoryConfig

# Simulate "novel" project setup
config = MemoryConfig(project_name="novel", repo_id="novel")
config.storage.backend = "neo4j"

memory = Memory(config=config)

print("=" * 60)
print("TESTING NOVEL PROJECT")
print("=" * 60)

# Test 1: Learn (semantic memory)
print("\n1. Testing 'learn' command...")
try:
    mem_id = memory.learn("The protagonist discovers hidden magic", category="fact")
    print(f"✓ Created semantic memory: {mem_id}")
except Exception as e:
    print(f"✗ FAILED: {e}")

# Test 2: Record (epistodic memory) for comparison
print("\n2. Testing 'record' command...")
try:
    mem_id2 = memory.record("Chapter 1 written")
    print(f"✓ Created episodic memory: {mem_id2}")
except Exception as e:
    print(f"✗ FAILED: {e}")

# Test 3: Stats
print("\n3. Testing 'stats' command...")
try:
    stats = memory.stats()
    print(f"✓ Stats retrieved:")
    print(f"   - Total memories: {stats.get('total_memories', 0)}")
    print(f"   - By layer: {stats.get('memories_by_layer', {})}")
    print(f"   - Active intents: {stats.get('active_intents', 0)}")
except Exception as e:
    print(f"✗ FAILED: {e}")

# Test 4: Recall with semantic search
print("\n4. Testing 'recall' command...")
try:
    results = memory.recall("magic")
    print(f"✓ Recall found {len(results)} results")
    for i, r in enumerate(results[:3], 1):
        print(f"   {i}. [{r.get('layer')}] {r.get('content', '')[:50]}")
except Exception as e:
    print(f"✗ FAILED: {e}")

# Test 5: Direct storage query to verify repo_id
print("\n5. Verifying repo_id in database...")
try:
    with memory._storage.driver.session() as session:
        result = session.run("""
            MATCH (m:Memory {repo_id: $repo_id})
            RETURN m.layer as layer, count(m) as count
        """, repo_id="novel")
        counts = {rec["layer"]: rec["count"] for rec in result}
        print(f"✓ Memories with repo_id='novel': {counts}")
        
        # Also check if there are memories without repo_id
        result2 = session.run("""
            MATCH (m:Memory)
            WHERE m.repo_id IS NULL OR m.repo_id = ''
            RETURN m.layer as layer, count(m) as count
        """)
        null_counts = {rec["layer"]: rec["count"] for rec in result2}
        if null_counts:
            print(f"⚠ WARNING: Memories without repo_id: {null_counts}")
except Exception as e:
    print(f"✗ FAILED: {e}")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
