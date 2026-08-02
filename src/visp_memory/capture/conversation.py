"""
Conversation Capture Module

Parses LLM conversation logs (e.g. from Claude Desktop, ChatGPT exports)
to extract structured memories.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from visp_memory.capture.git import CaptureManifest, capture_content_hash
from visp_memory.core.beliefs import map_producer_belief_type
from visp_memory.core.llm import LLMClient, create_llm_client
from visp_memory.core.memory import Memory
from visp_memory.core.trust import Provenance, WriteChannel

logger = logging.getLogger(__name__)


class ConversationCapture:
    """Captures memories from conversation logs."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.config = memory.config.capture
        self._client: Optional[LLMClient] = None

    def _get_client(self) -> LLMClient:
        if self._client:
            return self._client

        provider = self.config.llm_provider
        # Fallback to compression provider if not set in capture
        if not provider:
            provider = self.memory.config.compression.llm_provider

        if not provider:
            raise ValueError(
                "No LLM provider configured for conversation capture. "
                "Set VISP_MEMORY_CAPTURE_LLM_PROVIDER."
            )

        self._client = create_llm_client(
            provider=provider,
            model=self.config.llm_model or self.memory.config.compression.llm_model,
            # Reuse generic key if applicable, or rely on env.
            api_key=self.memory.config.embedding.api_key,
        )
        return self._client

    def parse_file(self, path: str, dry_run: bool = False) -> Dict[str, Any]:
        """Parse a conversation file and record memories."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        content = path.read_text(encoding="utf-8")
        return self.parse_text(content, source=path.name, dry_run=dry_run)

    def parse_text(
        self, text: str, source: str = "conversation", dry_run: bool = False
    ) -> Dict[str, Any]:
        """Parse conversation text and record memories."""
        manifest = CaptureManifest(self.memory)
        content_hash = capture_content_hash({"source": source, "text": text})
        status, entry = manifest.check("conversation", source, content_hash)
        if status == "unchanged" and not dry_run:
            return {
                "decisions": 0,
                "learnings": 0,
                "bugs": 0,
                "tasks": 0,
                "raw": {},
                "capture_manifest": {
                    "status": "unchanged",
                    "output_memory_ids": entry.get("output_memory_ids", []) if entry else [],
                },
            }

        client = self._get_client()

        system_prompt = """You are a Memory Extraction System.
Your goal is to analyze the user's conversation with an AI and extract structured memories.

Extract these categories:
1. DECISIONS: Key architectural or design choices made (what, why, alternatives).
2. LEARNINGS: New facts, patterns, or technical knowledge gained.
3. BUGS: Fixed bugs or discovered issues (description, cause, fix).
4. TASKS: Items marked as todo or future work.

Output strictly valid JSON with this schema:
{
  "decisions": [{"what": "...", "why": "...", "alternatives": ["..."]}],
  "learnings": [{"knowledge": "...", "category": "fact/pattern", "importance": 0.0-1.0}],
  "bugs": [{"description": "...", "cause": "...", "fix": "..."}],
  "tasks": [{"description": "...", "status": "todo"}]
}
If nothing relevant is found for a category, return an empty list.
"""

        prompt = (
            "Analyze this conversation log and extract memories:\n\n"
            f"{text[:20000]}"
        )

        try:
            response = client.completion(
                prompt=prompt,
                system_prompt=system_prompt,
                response_format={"type": "json_object"}
                if hasattr(client, "client") and hasattr(client.client, "chat")
                else None,  # Hint for OpenAI
            )

            # Clean response if markdown code block
            clean_resp = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_resp)

            summary = {
                "decisions": len(data.get("decisions", [])),
                "learnings": len(data.get("learnings", [])),
                "bugs": len(data.get("bugs", [])),
                "tasks": len(data.get("tasks", [])),
                "raw": data,
            }

            if dry_run:
                return summary

            # Record Memories
            repo_id = self.memory.config.repo_id
            output_memory_ids = []

            for d in data.get("decisions", []):
                memory_id = self.memory.decision(
                    what=d["what"],
                    why=d.get("why", "Unknown"),
                    alternatives=d.get("alternatives"),
                    repo_id=repo_id,
                    _write_channel=WriteChannel.CONVERSATION,
                )
                output_memory_ids.append(str(memory_id))

            for learning in data.get("learnings", []):
                legacy_category = learning.get("category", "fact")
                memory_id = self.memory.learn(
                    knowledge=learning["knowledge"],
                    category=map_producer_belief_type(legacy_category),
                    importance=learning.get("importance", 0.5),
                    repo_id=repo_id,
                    tags=[f"legacy_category:{legacy_category}"],
                    _write_channel=WriteChannel.CONVERSATION,
                )
                output_memory_ids.append(str(memory_id))

            for b in data.get("bugs", []):
                # episodic.record() accepts `context` (not `metadata`), and
                # "bug_found" is the valid EpisodeCategory for a discovered bug.
                memory_id = self.memory.record(
                    event=f"Bug: {b['description']}",
                    category="bug_found",
                    context={"cause": b.get("cause"), "fix": b.get("fix")},
                    repo_id=repo_id,
                    _write_channel=WriteChannel.CONVERSATION,
                )
                output_memory_ids.append(str(memory_id))

            for task in data.get("tasks", []):
                memory_id = self.memory.intent.set_goal(
                    goal=task["description"],
                    repo_id=repo_id,
                    context={
                        "captured_as": "task",
                        "source": source,
                        "status": task.get("status", "todo"),
                        "provenance": Provenance.ASSISTED.value,
                        "write_channel": WriteChannel.CONVERSATION.value,
                    },
                )
                output_memory_ids.append(str(memory_id))

            manifest.record("conversation", source, content_hash, output_memory_ids, status=status)
            summary["capture_manifest"] = {
                "status": status,
                "output_memory_ids": output_memory_ids,
            }

            return summary

        except Exception as e:
            logger.error(f"Failed to parse conversation: {e}")
            raise
