
import logging
from llm_memory.core.neo4j_storage import Neo4jStorage

logging.basicConfig(level=logging.INFO)

def verify():
    print("Testing Neo4j Connection...")
    try:
        storage = Neo4jStorage()
        print("Connected successfully.")
        
        # Test 1: Store
        mid = storage.store_memory("Neo4j Verification Test", layer="episodic", category="test")
        print(f"Stored verified memory: {mid}")
        
        # Test 2: Get
        mem = storage.get_memory(mid)
        if mem and mem["content"] == "Neo4j Verification Test":
            print("Retrieval verified.")
        else:
            print(f"Retrieval FAILED. Got: {mem}")
            
        # Test 3: Delete
        storage.delete_memory(mid)
        print("Cleanup complete.")
        
        storage.close()
        print("Neo4j Verification PASSED.")
    except Exception as e:
        print(f"Neo4j Verification FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify()
