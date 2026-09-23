import json
import shlex
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml


@pytest.mark.asyncio
async def test_ci_session_smoke_uses_a_valid_scoped_request(client):
    workflow = yaml.safe_load(
        (Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["docker-neo4j-smoke"]["steps"]
    script = next(
        step["run"] for step in steps if step.get("name") == "Verify readiness and core routes"
    )
    command = next(line for line in script.replace("\\\n", "").splitlines() if "/sessions" in line)
    args = shlex.split(command)
    payload = json.loads(args[args.index("-d") + 1]) if "-d" in args else None
    url = next(arg for arg in args if arg.startswith("http://"))
    response = await client.post(
        urlsplit(url).path,
        json=payload,
        headers={"X-API-KEY": "test_key"},
    )
    assert response.status_code == 200, response.text
