
import requests
import json
import time

BASE_URL = "http://localhost:8001"

def verify():
    print(f"Testing Phase 2 Endpoints at {BASE_URL}...")
    try:
        # 1. Create a Memory to manipulate
        payload = {
            "content": "Phase 2 Test Memory",
            "layer": "episodic",
            "importance": 0.5
        }
        resp = requests.post(f"{BASE_URL}/memories", json=payload)
        resp.raise_for_status()
        mem_id = resp.json()["id"]
        print(f"Created Memory: {mem_id}")

        # 2. Get Memory
        resp = requests.get(f"{BASE_URL}/memories/{mem_id}")
        resp.raise_for_status()
        data = resp.json()
        assert data["id"] == mem_id
        assert data["content"] == "Phase 2 Test Memory"
        print("GET /memories/{id} PASSED")

        # 3. Patch Memory
        patch_payload = {
            "content": "Phase 2 Test Memory UPDATED",
            "importance": 0.9
        }
        resp = requests.patch(f"{BASE_URL}/memories/{mem_id}", json=patch_payload)
        resp.raise_for_status()
        
        # Verify Update
        resp = requests.get(f"{BASE_URL}/memories/{mem_id}")
        data = resp.json()
        assert data["content"] == "Phase 2 Test Memory UPDATED"
        assert data["importance"] == 0.9
        print("PATCH /memories/{id} PASSED")

        # 4. Create another memory and link them
        resp = requests.post(f"{BASE_URL}/memories", json={"content": "Linked Memory"})
        target_id = resp.json()["id"]
        
        rel_payload = {
            "source_id": mem_id,
            "target_id": target_id,
            "relationship": "TEST_LINK",
            "strength": 0.8
        }
        resp = requests.post(f"{BASE_URL}/relationships", json=rel_payload)
        resp.raise_for_status()
        print("POST /relationships PASSED")

        # 5. Delete Memory
        resp = requests.delete(f"{BASE_URL}/memories/{mem_id}")
        resp.raise_for_status()
        
        # Verify Deletion
        resp = requests.get(f"{BASE_URL}/memories/{mem_id}")
        assert resp.status_code == 404
        print("DELETE /memories/{id} PASSED")
        
        # Clean up second memory
        requests.delete(f"{BASE_URL}/memories/{target_id}")

        print("Phase 2 Verification PASSED.")
        
    except Exception as e:
        print(f"Phase 2 Verification FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify()
