
import requests
import json
import time

BASE_URL = "http://localhost:8001"

def verify():
    print(f"Testing Server at {BASE_URL}...")
    try:
        # 1. Health Check
        resp = requests.get(f"{BASE_URL}/")
        resp.raise_for_status()
        print("Health Check PASSED.")

        # 2. Create Memory
        payload = {
            "content": "Server Verification Test",
            "layer": "episodic",
            "category": "test",
            "importance": 0.8
        }
        resp = requests.post(f"{BASE_URL}/memories", json=payload)
        resp.raise_for_status()
        mem_data = resp.json()
        print(f"Create Memory PASSED: {mem_data['id']}")
        
        # 3. List Memories
        resp = requests.get(f"{BASE_URL}/memories")
        resp.raise_for_status()
        memories = resp.json()
        found = any(m["id"] == mem_data["id"] for m in memories)
        if found:
            print("List Memories PASSED.")
        else:
            print("List Memories FAILED: Created memory not found.")
            
        # 4. Create Intent
        payload = {
            "description": "Verify intent creation",
            "priority": 5,
            "context": {"test": True}
        }
        resp = requests.post(f"{BASE_URL}/intents", json=payload)
        resp.raise_for_status()
        intent_data = resp.json()
        print(f"Create Intent PASSED: {intent_data['id']}")
        
        # 5. Get Graph
        resp = requests.get(f"{BASE_URL}/graph")
        resp.raise_for_status()
        graph = resp.json()
        if "nodes" in graph and "links" in graph:
            print(f"Get Graph PASSED: {len(graph['nodes'])} nodes, {len(graph['links'])} links.")
        else:
            print("Get Graph FAILED: Invalid format.")
            
        print("Server Verification PASSED.")
        
    except Exception as e:
        print(f"Server Verification FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify()
