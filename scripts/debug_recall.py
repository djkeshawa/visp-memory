
import logging
import sys
from llm_memory.core.neo4j_storage import Neo4jStorage

# Configure logging
logging.basicConfig(level=logging.DEBUG)

try:
    print("Initializing Storage...")
    storage = Neo4jStorage()
    
    print("Searching memories...")
    results = storage.search_memories(query="project", limit=5)
    
    print(f"Found {len(results)} results")
    for r in results:
        print(f"- {r['content'][:50]}...")
        
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    if 'storage' in locals():
        storage.close()
